"""三臂编排：A 等整段再合成 / B 按句流式 / C 固定窗口流式。

方法学口径（关键）：
  每题**只调一次 LLM**，三臂共享同一份 chunk 时间戳序列。
  - full：全文就绪（最后 chunk 到达）后才做整段合成——真实执行；
  - stream/fixed：从 t0 起按 chunk 时间戳回放，段就绪即提交合成——真实执行。
  LLM 生成速度的随机波动因此不进入臂间对比；TTS 合成耗时仍然真实测量。
  时间线 = 真实 LLM 时刻 + 真实合成耗时 + 纯计算的播放排队。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import llm, tts
from .config import Settings
from .player import SegEvent, Timeline, build_timeline
from .segment import FixedSegmenter, SentenceSegmenter

SYSTEM_PROMPT = (
    "你是一个语音助手。用中文口语回答用户问题，简短自然，"
    "不要用括号、编号和列表符号。"
)

ARM_NAMES = ("full", "stream", "fixed")


@dataclass
class ChunkStream:
    """一次 LLM 调用的完整产物，三臂共享。"""

    text: str
    chunks: list[tuple[float, str]]
    t0: float
    ttft: float | None
    total_s: float
    usage: dict | None
    n_chunks: int


@dataclass
class ArmResult:
    arm: str
    qid: str
    text: str
    events: list[SegEvent] = field(default_factory=list)
    timeline: Timeline = field(default_factory=Timeline)
    llm: ChunkStream | None = None
    fidelity_ok: bool = True

    @property
    def synth_total_s(self) -> float:
        return round(sum(e.synth_s for e in self.events), 3)

    def to_dict(self) -> dict:
        tl = self.timeline
        return {
            "arm": self.arm,
            "qid": self.qid,
            "text": self.text,
            "chars": len(self.text),
            "n_segments": len(self.events),
            "ttfa_s": tl.ttfa,
            "makespan_s": tl.makespan,
            "pauses": tl.pauses,
            "pause_total_s": tl.pause_total_s,
            "audio_total_s": tl.audio_total_s,
            "idle_s": round(tl.idle_s, 3),
            "synth_total_s": self.synth_total_s,
            "llm_ttft_s": self.llm.ttft if self.llm else None,
            "llm_total_s": round(self.llm.total_s, 3) if self.llm else None,
            "n_chunks": self.llm.n_chunks if self.llm else 0,
            "usage": self.llm.usage if self.llm else None,
            "fidelity_ok": self.fidelity_ok,
            "audio_files": [e.wav for e in self.events],
            "timeline": tl.rows,
        }


def collect_chunks(qid: str, question: str, cfg: Settings) -> ChunkStream:
    """调一次 LLM，收集带时刻的 chunk 序列。失败重试一次。"""
    for attempt in (1, 2):
        t0 = time.perf_counter()
        res = llm.stream_chat(
            cfg.base_url, cfg.api_key, cfg.model, SYSTEM_PROMPT, question,
            temperature=cfg.temperature, max_tokens=cfg.max_tokens, t0=t0,
        )
        if res.text:
            return ChunkStream(
                text=res.text, chunks=res.chunks, t0=t0, ttft=res.ttft,
                total_s=res.total_s, usage=res.usage, n_chunks=res.n_chunks,
            )
        print(f"  [warn] {qid} attempt {attempt} empty, retry")
    raise RuntimeError(f"{qid}: LLM returned empty twice")


def _synth_one(
    t0: float, idx: int, text: str, ready_t: float, path: Path,
    voice_id: str | None, rate: int,
) -> SegEvent:
    start = time.perf_counter() - t0
    r = tts.synth(text, path, voice_id, rate)
    done = time.perf_counter() - t0
    return SegEvent(
        idx=idx, text=text, ready_t=ready_t,
        synth_start_t=round(start, 4), synth_done_t=round(done, 4),
        duration_s=r["duration_s"], synth_s=r["synth_s"], wav=path.name,
    )


def run_arm(
    arm: str, qid: str, cs: ChunkStream, cfg: Settings,
    audio_dir: Path, voice_id: str | None,
) -> ArmResult:
    audio_dir.mkdir(parents=True, exist_ok=True)
    if arm == "full":
        return _run_full(qid, cs, cfg, audio_dir, voice_id)
    if arm in ("stream", "fixed"):
        return _run_replay(arm, qid, cs, cfg, audio_dir, voice_id)
    raise ValueError(f"unknown arm: {arm}")


def _finish(arm: str, qid: str, cs: ChunkStream, events: list[SegEvent],
            cfg: Settings) -> ArmResult:
    tl = build_timeline(events, cfg.pause_threshold_s)
    joined = "".join(e.text for e in events)
    return ArmResult(
        arm=arm, qid=qid, text=joined, events=events, timeline=tl,
        llm=cs, fidelity_ok=(joined == cs.text),
    )


def _run_full(qid: str, cs: ChunkStream, cfg: Settings, audio_dir: Path,
              voice_id: str | None) -> ArmResult:
    """A 臂：全文就绪后才整段合成（真实执行）。"""
    ev = _synth_one(cs.t0, 0, cs.text, cs.total_s,
                    audio_dir / "seg-000.wav", voice_id, cfg.voice_rate)
    # ready_t 语义：全文最后字符到达时刻
    ev = SegEvent(idx=0, text=ev.text, ready_t=cs.total_s,
                  synth_start_t=ev.synth_start_t, synth_done_t=ev.synth_done_t,
                  duration_s=ev.duration_s, synth_s=ev.synth_s, wav=ev.wav)
    return _finish("full", qid, cs, [ev], cfg)


def _run_replay(arm: str, qid: str, cs: ChunkStream, cfg: Settings,
                audio_dir: Path, voice_id: str | None) -> ArmResult:
    """B/C 臂：按 chunk 时间戳回放，段就绪即提交合成（真实执行，线程池并发）。"""
    if arm == "stream":
        segmenter = SentenceSegmenter(cfg.max_sentence_len)
    else:
        segmenter = FixedSegmenter(cfg.fixed_window)

    t0 = time.perf_counter()
    events: list[SegEvent | None] = []
    futures = []

    def submit(seg: str, ready_t: float) -> None:
        idx = len(events)
        events.append(None)
        futures.append(
            ex.submit(_synth_one, t0, idx, seg, ready_t,
                      audio_dir / f"seg-{idx:03d}.wav", voice_id, cfg.voice_rate)
        )

    with ThreadPoolExecutor(max_workers=cfg.tts_concurrency) as ex:
        for t_rel, piece in cs.chunks:
            delay = t_rel - (time.perf_counter() - t0)
            if delay > 0:
                time.sleep(delay)  # 回放 LLM 供句节奏
            for seg in segmenter.feed(piece):
                submit(seg, t_rel)
        for seg in segmenter.flush():
            submit(seg, cs.chunks[-1][0] if cs.chunks else 0.0)
        for f in futures:
            ev = f.result()
            events[ev.idx] = ev

    evs = [e for e in events if e is not None]
    return _finish(arm, qid, cs, evs, cfg)
