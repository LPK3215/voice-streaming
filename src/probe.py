"""环境与能力探测：SAPI 音色枚举、wav 合成、多线程合成、LLM 流式。

跑法：python -m src.probe
结果同时打印并写入 runs/probe-<ts>/probe.json。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def _wave_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


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


def synth(text: str, path: Path, voice_id: str | None = None, rate: int = 0) -> dict:
    """用 SAPI 把 text 合成到 path。

    Speak() 是异步提交，返回不等于合成完；真实合成耗时用 WaitForDone 量。
    """
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
    submitted = time.perf_counter() - t0
    try:
        vc.WaitForDone(-1)
    except AttributeError:
        pass
    elapsed = time.perf_counter() - t0
    fs.Close()
    vc.AudioOutputStream = None
    return {"submitted_s": round(submitted, 3), "elapsed_s": round(elapsed, 3),
            "duration_s": round(_wave_duration(path), 3),
            "size_bytes": path.stat().st_size}


def probe_sapi(out: dict, tmp: Path) -> None:
    voices = list_voices()
    out["voices"] = voices
    zh = [v for v in voices if "804" in (v.get("lang") or "")]
    out["zh_voices"] = [v["desc"] for v in zh]
    if not zh:
        out["sapi_ok"] = False
        out["sapi_error"] = "no zh voice"
        return
    target = zh[0]
    out["target_voice"] = target

    p = tmp / "main.wav"
    out["main_thread"] = synth("语音流式响应实验，这是主线程合成测试。", p, target["id"])

    # 子线程合成：COM 线程模型探测
    err = {}

    def worker(name: str, text: str) -> None:
        try:
            p2 = tmp / f"{name}.wav"
            err[name] = synth(text, p2, target["id"])
        except Exception as e:  # noqa: BLE001
            err[name] = {"error": repr(e)}

    t = threading.Thread(target=worker, args=("thread", "这是子线程合成测试。"))
    t.start()
    t.join()
    out["worker_thread"] = err.get("thread")

    # 并发合成探测：两个 SpVoice 实例同时合成，看总耗时接近 max 还是 sum
    t0 = time.perf_counter()
    threads = [
        threading.Thread(target=worker, args=("concurrent_a",
                                               "并发甲号线程，测试两个语音对象同时合成是否互相干扰。")),
        threading.Thread(target=worker, args=("concurrent_b",
                                               "并发乙号线程，测试两个语音对象同时合成是否互相干扰。")),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    out["concurrent"] = {"wall_s": round(time.perf_counter() - t0, 3),
                          "a": err.get("concurrent_a"), "b": err.get("concurrent_b")}
    out["sapi_ok"] = True


def probe_llm(out: dict, model: str, base_url: str, api_key: str) -> None:
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)
    t0 = time.perf_counter()
    first = None
    chunks = 0
    text = []
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "用三句话介绍什么是 VAD。"}],
        stream=True,
        stream_options={"include_usage": True},
        temperature=0,
    )
    usage = None
    for chunk in stream:
        chunks += 1
        if first is None and chunk.choices and chunk.choices[0].delta.content:
            first = time.perf_counter() - t0
        if chunk.choices and chunk.choices[0].delta.content:
            text.append(chunk.choices[0].delta.content)
        if getattr(chunk, "usage", None):
            usage = chunk.usage.model_dump()
    total = time.perf_counter() - t0
    out["llm"] = {
        "model": model,
        "ttft_s": round(first, 3) if first else None,
        "total_s": round(total, 3),
        "chunks": chunks,
        "chars": len("".join(text)),
        "usage": usage,
    }


def main() -> int:
    tmp = RUNS / f"probe-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    tmp.mkdir(parents=True, exist_ok=True)

    # .env 读取（不依赖 python-dotenv）
    env_path = ROOT / ".env"
    cfg: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()

    out: dict = {"time": datetime.now().isoformat(timespec="seconds"),
                 "python": sys.version.split()[0]}
    try:
        probe_sapi(out, tmp)
    except Exception as e:  # noqa: BLE001
        out["sapi_ok"] = False
        out["sapi_error"] = repr(e)

    model = cfg.get("OPENAI_MODEL", "")
    try:
        if model and model != "<provider>/<model>":
            probe_llm(out, model, cfg.get("OPENAI_BASE_URL", ""),
                      cfg.get("OPENAI_API_KEY", ""))
    except Exception as e:  # noqa: BLE001
        out["llm_error"] = repr(e)

    (tmp / "probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nprobe dir: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
