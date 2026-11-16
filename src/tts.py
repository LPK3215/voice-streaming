"""Windows SAPI 离线 TTS：wav 合成与真实播放。

要点：Speak() 是异步提交；写 SpFileStream 时 SAPI 离线渲染，
耗时（~10-50ms）远小于音频时长（秒级），两者解耦。
每次合成新建 SpVoice 实例，多线程并发安全（2026-11-16 probe 实测）。
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import winsound


def list_voices() -> list[dict]:
    import win32com.client

    vc = win32com.client.Dispatch("SAPI.SpVoice")
    out = []
    for tok in vc.GetVoices():
        out.append(
            {
                "desc": tok.GetDescription(),
                "lang": tok.GetAttribute("Language"),
                "gender": tok.GetAttribute("Gender"),
                "id": tok.Id,
            }
        )
    return out


def wave_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def pick_voice(lang: str = "804") -> str | None:
    """按 SAPI Language 属性选音色 id；找不到返回 None（用默认）。"""
    try:
        for v in list_voices():
            if lang in (v.get("lang") or ""):
                return v["id"]
    except Exception:  # noqa: BLE001
        pass
    return None


def synth(text: str, path: Path, voice_id: str | None = None, rate: int = 0) -> dict:
    """合成 text 到 wav。返回合成耗时（含提交）、音频时长、字节数。"""
    import win32com.client

    vc = win32com.client.Dispatch("SAPI.SpVoice")
    if voice_id:
        for tok in vc.GetVoices():
            if tok.Id == voice_id:
                vc.Voice = tok
                break
    vc.Rate = rate
    fs = win32com.client.Dispatch("SAPI.SpFileStream")
    fs.Open(str(path), 3)  # SSFMCreateForWrite
    vc.AudioOutputStream = fs
    t0 = time.perf_counter()
    vc.Speak(text)
    try:
        vc.WaitForDone(-1)
    except Exception:  # noqa: BLE001
        pass
    elapsed = time.perf_counter() - t0
    fs.Close()
    vc.AudioOutputStream = None
    path.parent.mkdir(parents=True, exist_ok=True)
    return {
        "synth_s": round(elapsed, 4),
        "duration_s": round(wave_duration(path), 4),
        "size_bytes": path.stat().st_size,
    }


def play(path: Path, wait: bool = True) -> None:
    """真实播放（演示用）。winsound 同步阻塞至播完。"""
    winsound.PlaySound(str(path), winsound.SND_FILENAME | (winsound.SND_SYNC if wait else 0))
