"""命令行入口：selftest / bench / play / demo / probe。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import tts
from .bench import run_bench
from .config import Settings
from .pipeline import collect_chunks, run_arm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_questions() -> list[dict]:
    return json.loads((DATA / "questions.json").read_text(encoding="utf-8"))


def cmd_selftest(_args) -> int:
    from .selftest import main as selftest_main
    return selftest_main()


def cmd_bench(args) -> int:
    cfg = Settings.from_env()
    questions = load_questions()
    if args.limit:
        questions = questions[: args.limit]
    arms = args.arms.split(",") if args.arms else None
    out = Path(args.out) if args.out else None
    summary = run_bench(cfg, questions, arms=arms, out_dir=out)
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:2000])
    return 0


def cmd_play(args) -> int:
    wav = Path(args.wav)
    if not wav.exists():
        print(f"not found: {wav}")
        return 1
    tts.play(wav)
    return 0


def cmd_demo(args) -> int:
    """单题三臂冒烟：跑一题（一次 LLM 调用，三臂共享），打印时间线对照。"""
    cfg = Settings.from_env()
    questions = load_questions()
    q = next((x for x in questions if x["id"] == args.qid), questions[0])
    voice_id = tts.pick_voice(cfg.voice_lang)
    out_dir = Path(args.out) if args.out else ROOT / "runs" / "demo"
    cs = collect_chunks(q["id"], q["q"], cfg)
    print(f"llm: ttft={cs.ttft}s total={cs.total_s:.1f}s "
          f"chunks={cs.n_chunks} chars={len(cs.text)}")
    rows = []
    for arm in ("full", "stream", "fixed"):
        res = run_arm(arm, q["id"], cs, cfg,
                      out_dir / f"{q['id']}-{arm}", voice_id)
        rows.append(res)
        print(f"[{arm}] ttfa={res.timeline.ttfa}s makespan={res.timeline.makespan}s "
              f"segs={len(res.events)} pauses={res.timeline.pauses} "
              f"fidelity={res.fidelity_ok}")
    print(f"\ntext ({len(rows[0].text)} chars): {rows[0].text[:120]}...")
    return 0


def cmd_probe(_args) -> int:
    from .probe import main as probe_main
    return probe_main()


def main() -> int:
    parser = argparse.ArgumentParser(prog="voice-streaming")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="机制自测（不调 LLM）").set_defaults(func=cmd_selftest)

    p_bench = sub.add_parser("bench", help="批量对照")
    p_bench.add_argument("--arms", default="full,stream,fixed", help="逗号分隔")
    p_bench.add_argument("--limit", type=int, default=0)
    p_bench.add_argument("--out", default="")
    p_bench.set_defaults(func=cmd_bench)

    p_play = sub.add_parser("play", help="真实播放 wav")
    p_play.add_argument("wav")
    p_play.set_defaults(func=cmd_play)

    p_demo = sub.add_parser("demo", help="单题三臂冒烟")
    p_demo.add_argument("--qid", default="")
    p_demo.add_argument("--out", default="")
    p_demo.set_defaults(func=cmd_demo)

    sub.add_parser("probe", help="环境探测（SAPI / LLM）").set_defaults(func=cmd_probe)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
