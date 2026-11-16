"""播放时间线：把分段合成事件折算成可播时刻表。

播放调度规则（纯计算，无不确定性）：
  段 i 可播时刻 = max(该段合成完成时刻, 上一段播完时刻)
指标：
  ttfa       首段可播时刻（用户等到第一声的时间）
  makespan   末段播完时刻
  pauses     相邻段间隔 > 阈值的次数
  audio_total 音频总时长（与 makespan 的差 = 等待合成的空档）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SegEvent:
    idx: int
    text: str
    ready_t: float        # 该段最后字符到达时刻
    synth_start_t: float  # 合成开始时刻
    synth_done_t: float   # 合成完成时刻
    duration_s: float     # 音频时长
    synth_s: float        # 合成耗时
    wav: str = ""


@dataclass
class Timeline:
    segs: list[SegEvent] = field(default_factory=list)
    ttfa: float | None = None
    makespan: float | None = None
    pauses: int = 0
    pause_total_s: float = 0.0
    audio_total_s: float = 0.0
    rows: list[dict] = field(default_factory=list)

    @property
    def idle_s(self) -> float:
        """makespan 里没声音的空档总长。"""
        if self.makespan is None:
            return 0.0
        return self.makespan - self.audio_total_s


def build_timeline(segs: list[SegEvent], pause_threshold: float = 0.15) -> Timeline:
    segs = sorted(segs, key=lambda s: s.idx)
    tl = Timeline(segs=segs)
    prev_end = 0.0  # t0 = 发送问题时刻
    first = True
    for s in segs:
        begin = max(s.synth_done_t, prev_end)
        end = begin + s.duration_s
        # 首段之前的等待是 TTFA，不算播放停顿
        gap = 0.0 if first else begin - prev_end
        first = False
        tl.rows.append(
            {
                "idx": s.idx,
                "begin_s": round(begin, 3),
                "end_s": round(end, 3),
                "gap_s": round(gap, 3),
                "duration_s": round(s.duration_s, 3),
                "synth_done_s": round(s.synth_done_t, 3),
                "ready_s": round(s.ready_t, 3),
                "text": s.text,
            }
        )
        if gap > pause_threshold:
            tl.pauses += 1
            tl.pause_total_s += gap
        prev_end = end
        tl.audio_total_s += s.duration_s
    if tl.rows:
        tl.ttfa = tl.rows[0]["begin_s"]
        tl.makespan = round(prev_end, 3)
    tl.audio_total_s = round(tl.audio_total_s, 3)
    tl.pause_total_s = round(tl.pause_total_s, 3)
    return tl
