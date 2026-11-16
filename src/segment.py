"""分句器：B 臂按标点（语义边界），C 臂按固定窗口（不做语义判断）。

流式接口：feed(piece) 返回本次切出的完整段，flush() 收尾残余。
时间线起点 t0 = 发送问题时刻，所有时刻相对 t0 记秒。
"""

from __future__ import annotations

SENTENCE_END = "。！？；!?;\n"
CLAUSE_END = "，,、：:"


class SentenceSegmenter:
    """遇到句末标点即切出；超长句（>max_len）先按逗号二次切分，防止单段音频过长。"""

    def __init__(self, max_len: int = 80):
        self.max_len = max_len
        self.buf = ""

    def feed(self, piece: str) -> list[str]:
        self.buf += piece
        out: list[str] = []
        # 先拆超长：在 max_len 窗口内找最后一个逗号/冒号，找不到硬切
        while len(self.buf) > self.max_len:
            window = self.buf[: self.max_len]
            cut = -1
            for c in CLAUSE_END:
                pos = window.rfind(c)
                if pos > cut:
                    cut = pos
            if cut <= 0:
                cut = self.max_len - 1
            out.append(self.buf[: cut + 1])
            self.buf = self.buf[cut + 1 :]
        # 再按句末标点切
        while True:
            idx = -1
            for i, ch in enumerate(self.buf):
                if ch in SENTENCE_END:
                    idx = i
                    break
            if idx < 0:
                break
            out.append(self.buf[: idx + 1])
            self.buf = self.buf[idx + 1 :]
        return out

    def flush(self) -> list[str]:
        out = [self.buf] if self.buf.strip() else []
        self.buf = ""
        return out


class FixedSegmenter:
    """固定窗口切分：每 window 字符一段，刻意不在语义边界断（C 臂对照）。"""

    def __init__(self, window: int = 20):
        self.window = window
        self.buf = ""

    def feed(self, piece: str) -> list[str]:
        self.buf += piece
        out: list[str] = []
        while len(self.buf) >= self.window:
            out.append(self.buf[: self.window])
            self.buf = self.buf[self.window :]
        return out

    def flush(self) -> list[str]:
        out = [self.buf] if self.buf.strip() else []
        self.buf = ""
        return out
