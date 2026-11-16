"""批量基准：同一批问题 × 三臂，逐题落盘 + 汇总。

每题只调一次 LLM（collect_chunks），三臂共享 chunk 时间戳：
消除 LLM 生成速度随机波动对臂间对比的污染。串行执行，时延数字无 CPU 竞争。
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

from . import tts
from .config import Settings
from .pipeline import ARM_NAMES, collect_chunks, run_arm


def _p(values: list[float], q: float = 0.5) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return round(statistics.median(vals), 3)


def aggregate(per_query: list[dict], questions: list[dict]) -> dict:
    arms = sorted({r["arm"] for r in per_query})
    out: dict = {"arms": {}, "by_tier": {}, "pairs": {}}
    for arm in arms:
        rows = [r for r in per_query if r["arm"] == arm]
        ttfa = [r["ttfa_s"] for r in rows]
        mk = [r["makespan_s"] for r in rows]
        tok_in = sum((r["usage"] or {}).get("prompt_tokens", 0) for r in rows)
        tok_out = sum((r["usage"] or {}).get("completion_tokens", 0) for r in rows)
        out["arms"][arm] = {
            "n": len(rows),
            "ttfa_p50_s": _p(ttfa),
            "ttfa_mean_s": round(statistics.fmean([v for v in ttfa if v is not None]), 3) if any(v is not None for v in ttfa) else None,
            "ttfa_max_s": round(max(v for v in ttfa if v is not None), 3) if any(v is not None for v in ttfa) else None,
            "makespan_p50_s": _p(mk),
            "makespan_mean_s": round(statistics.fmean([v for v in mk if v is not None]), 3) if any(v is not None for v in mk) else None,
            "pauses_mean": round(statistics.fmean([r["pauses"] for r in rows]), 2) if rows else None,
            "pauses_total": sum(r["pauses"] for r in rows),
            "segments_mean": round(statistics.fmean([r["n_segments"] for r in rows]), 2) if rows else None,
            "chars_mean": round(statistics.fmean([r["chars"] for r in rows]), 1) if rows else None,
            "audio_total_mean_s": round(statistics.fmean([r["audio_total_s"] for r in rows]), 2) if rows else None,
            "idle_mean_s": round(statistics.fmean([r["idle_s"] for r in rows]), 2) if rows else None,
            "llm_total_mean_s": round(statistics.fmean([r["llm_total_s"] for r in rows]), 2) if rows else None,
            "llm_ttft_p50_s": _p([r["llm_ttft_s"] for r in rows]),
            "tokens_in": tok_in,
            "tokens_out": tok_out,
            "tokens_per_q": round((tok_in + tok_out) / len(rows)) if rows else None,
            "fidelity_all_ok": all(r["fidelity_ok"] for r in rows),
        }
    for tier in ("short", "medium", "long"):
        ids = {q["id"] for q in questions if q["tier"] == tier}
        sel = [r for r in per_query if r["qid"] in ids]
        if not sel:
            continue
        per = {}
        for arm in arms:
            rows = [r for r in sel if r["arm"] == arm]
            per[arm] = {
                "n": len(rows),
                "ttfa_p50_s": _p([r["ttfa_s"] for r in rows]),
                "ttfa_mean_s": round(statistics.fmean([r["ttfa_s"] for r in rows if r["ttfa_s"] is not None]), 3) if any(r["ttfa_s"] is not None for r in rows) else None,
                "makespan_mean_s": round(statistics.fmean([r["makespan_s"] for r in rows if r["makespan_s"] is not None]), 2) if any(r["makespan_s"] is not None for r in rows) else None,
                "pauses_mean": round(statistics.fmean([r["pauses"] for r in rows]), 2) if rows else None,
            }
        out["by_tier"][tier] = per
    # 成对：stream/fixed 相对 full 的 ttfa 提前量（同题共享 LLM 输出）
    base_rows = {r["qid"]: r for r in per_query if r["arm"] == "full"}
    for other in arms:
        if other == "full":
            continue
        gains = []
        for r in per_query:
            if r["arm"] != other:
                continue
            b = base_rows.get(r["qid"])
            if b and b["ttfa_s"] is not None and r["ttfa_s"] is not None:
                gains.append(round(b["ttfa_s"] - r["ttfa_s"], 3))
        if gains:
            out["pairs"][f"{other}_vs_full"] = {
                "n": len(gains),
                "gain_mean_s": round(statistics.fmean(gains), 3),
                "gain_p50_s": _p(gains),
                "negative_count": sum(1 for g in gains if g < 0),
            }
    return out


def run_bench(
    cfg: Settings,
    questions: list[dict],
    arms: list[str] | None = None,
    out_dir: Path | None = None,
) -> dict:
    arms = arms or list(ARM_NAMES)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = out_dir or Path(__file__).resolve().parent.parent / "runs" / f"bench-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_root = out_dir / "audio"
    qpath = out_dir / "per_query.jsonl"
    if qpath.exists():
        qpath.unlink()  # 同目录重跑不追加

    voice_id = tts.pick_voice(cfg.voice_lang)
    meta = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "model": cfg.model,
        "base_url": cfg.base_url,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "voice_lang": cfg.voice_lang,
        "voice_id": voice_id,
        "tts_concurrency": cfg.tts_concurrency,
        "pause_threshold_s": cfg.pause_threshold_s,
        "max_sentence_len": cfg.max_sentence_len,
        "fixed_window": cfg.fixed_window,
        "serial": True,
        "shared_llm_stream": True,
        "arms": arms,
        "n_questions": len(questions),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    per_query: list[dict] = []

    def record(res) -> None:
        d = res.to_dict()
        d["tier"] = next((q["tier"] for q in questions if q["id"] == res.qid), None)
        per_query.append(d)
        with qpath.open("a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    for q in questions:
        print(f"[{q['id']}/{q['tier']}] 收集 LLM 流…")
        cs = collect_chunks(q["id"], q["q"], cfg)
        print(f"  llm: ttft={cs.ttft}s total={cs.total_s:.1f}s "
              f"chunks={cs.n_chunks} chars={len(cs.text)}")
        for arm in arms:
            res = run_arm(arm, q["id"], cs, cfg,
                          audio_root / f"{q['id']}-{arm}", voice_id)
            record(res)
            print(f"  {arm:6s} ttfa={res.timeline.ttfa}s "
                  f"makespan={res.timeline.makespan}s segs={len(res.events)} "
                  f"pauses={res.timeline.pauses} fidelity={res.fidelity_ok}")

    summary = aggregate(per_query, questions)
    (out_dir / "summary.json").write_text(
        json.dumps({"meta": meta, "summary": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nbench done: {out_dir}")
    return summary
