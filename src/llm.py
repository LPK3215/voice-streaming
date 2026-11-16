"""LLM 流式调用：记录每个 chunk 的到达时间，返回带时间戳的文本流。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from openai import OpenAI


@dataclass
class StreamResult:
    text: str
    chunks: list[tuple[float, str]] = field(default_factory=list)  # (相对秒, 文本片段)
    ttft: float | None = None       # 首个内容 chunk 的相对时间
    total_s: float = 0.0
    usage: dict | None = None
    n_chunks: int = 0


def stream_chat(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    question: str,
    temperature: float = 0.0,
    max_tokens: int = 512,
    on_chunk: Callable[[str, float], None] | None = None,
    t0: float | None = None,
) -> StreamResult:
    """流式调用，on_chunk(text_piece, t_rel) 在收到每个内容 chunk 时回调（相对秒）。

    t0 可传入外部基准（pipeline 用同一基准记 TTS 时刻）。
    """
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=120)
    if t0 is None:
        t0 = time.perf_counter()
    res = StreamResult(text="")
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        stream=True,
        stream_options={"include_usage": True},
        temperature=temperature,
        max_tokens=max_tokens,
    )
    for chunk in stream:
        if not chunk.choices:
            if getattr(chunk, "usage", None):
                res.usage = chunk.usage.model_dump()
            continue
        piece = chunk.choices[0].delta.content
        if not piece:
            continue
        t_rel = time.perf_counter() - t0
        res.chunks.append((t_rel, piece))
        res.text += piece
        res.n_chunks += 1
        if res.ttft is None:
            res.ttft = t_rel
        if on_chunk:
            on_chunk(piece, t_rel)
    res.total_s = time.perf_counter() - t0
    return res
