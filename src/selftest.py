"""机制自测：不调 LLM API，验证分句、时间线、SAPI 合成与配置加载。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from . import tts
from .config import Settings
from .player import SegEvent, build_timeline
from .segment import FixedSegmenter, SentenceSegmenter


def check_segmenter() -> list[str]:
    errs = []
    # 1. 多 piece 喂入，按句切
    seg = SentenceSegmenter(max_len=80)
    out = []
    for piece in ["语音链路", "分四段。", "延迟要分开", "记。"]:
        out += seg.feed(piece)
    out += seg.flush()
    got = "".join(out)
    want = "语音链路分四段。延迟要分开记。"
    if got != want:
        errs.append(f"segmenter join mismatch: {got!r} != {want!r}")
    if len(out) != 2:
        errs.append(f"expected 2 sentences, got {len(out)}: {out}")

    # 2. 保真：拼起来必须等于原文（含残余）
    seg = SentenceSegmenter(max_len=10)  # 故意小 max_len 触发逗号切分
    text = "第一句，比较长的一句在这里结束。第二句短。"
    pieces = [text[i:i + 3] for i in range(0, len(text), 3)]
    out = []
    for p in pieces:
        out += seg.feed(p)
    out += seg.flush()
    if "".join(out) != text:
        errs.append(f"fidelity lost: {''.join(out)!r} != {text!r}")

    # 3. 固定窗口：整除与余数
    fx = FixedSegmenter(window=4)
    out = fx.feed("abcdefgh") + fx.flush()
    if out != ["abcd", "efgh"]:
        errs.append(f"fixed split: {out!r}")
    fx = FixedSegmenter(window=4)
    out = fx.feed("abcdefg") + fx.flush()
    if out != ["abcd", "efg"]:
        errs.append(f"fixed remainder: {out!r}")

    # 4. 固定窗口跨语义边界（C 臂特征）：7 字用 4 窗，必从词中间断开
    fx = FixedSegmenter(window=4)
    out = fx.feed("今天天气真不错") + fx.flush()
    if out != ["今天天气", "真不错"]:
        errs.append(f"fixed semantic break: {out!r}")
    return errs


def check_timeline() -> list[str]:
    errs = []
    segs = [
        SegEvent(0, "甲", 1.0, 1.0, 1.1, 5.0, 0.1),
        SegEvent(1, "乙", 3.0, 3.2, 3.3, 4.0, 0.1),
        SegEvent(2, "丙", 6.0, 6.1, 6.2, 3.0, 0.1),
    ]
    tl = build_timeline(segs, pause_threshold=0.5)
    # 段0: 1.1 可播 -> 6.1 完；段1: max(3.3, 6.1)=6.1 起，gap=0，10.1 完；
    # 段2: max(6.2, 10.1)=10.1 起，gap=0
    if abs(tl.ttfa - 1.1) > 1e-6:
        errs.append(f"ttfa {tl.ttfa} != 1.1")
    if abs(tl.makespan - 13.1) > 1e-6:
        errs.append(f"makespan {tl.makespan} != 13.1")
    if tl.pauses != 0:
        errs.append(f"pauses {tl.pauses} != 0")
    # 制造停顿：段1 合成完成晚于段0播完
    segs2 = [
        SegEvent(0, "甲", 1.0, 1.0, 1.1, 1.0, 0.1),
        SegEvent(1, "乙", 2.0, 2.0, 5.0, 1.0, 3.0),
    ]
    tl2 = build_timeline(segs2, pause_threshold=0.5)
    # 段0: 1.1->2.1；段1 完成 5.0 > 2.1，gap=2.9 -> 1 次停顿
    if tl2.pauses != 1 or abs(tl2.rows[1]["gap_s"] - 2.9) > 1e-6:
        errs.append(f"pause detect: {tl2.pauses=} {tl2.rows[1]['gap_s']=}")
    return errs


def check_tts() -> list[str]:
    errs = []
    if sys.platform != "win32":
        return ["tts selftest skipped: not win32"]
    voice_id = tts.pick_voice("804")
    if not voice_id:
        return ["no zh voice found"]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "selftest.wav"
        r = tts.synth("自测合成。", p, voice_id)
        if r["duration_s"] <= 0:
            errs.append(f"bad duration: {r}")
        if r["synth_s"] <= 0:
            errs.append(f"bad synth time: {r}")
    return errs


def check_config() -> list[str]:
    errs = []
    cfg = Settings.from_env()
    if not cfg.model:
        errs.append("model is empty (.env missing?)")
    if not cfg.base_url:
        errs.append("base_url is empty")
    return errs


def main() -> int:
    errs = []
    errs += check_config()
    errs += check_segmenter()
    errs += check_timeline()
    errs += check_tts()
    if errs:
        print(f"SELFTEST FAIL ({len(errs)})")
        for e in errs:
            print(" -", e)
        return 1
    print("SELFTEST PASS: config / segment / timeline / tts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
