"""配置读取：.env 三件套 + 实验固定参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _find_env() -> Path | None:
    """从当前目录向上找 .env，最多三级。"""
    cur = ROOT
    for _ in range(3):
        p = cur / ".env"
        if p.exists():
            return p
        cur = cur.parent
    return None


def load_env() -> dict[str, str]:
    env_path = _find_env()
    cfg: dict[str, str] = {}
    if env_path:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    # 环境变量优先（CI 场景）
    for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


@dataclass
class Settings:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 512
    voice_lang: str = "804"          # SAPI 简体中文
    voice_rate: int = 0              # 语速
    tts_concurrency: int = 2         # B/C 臂并发合成的线程数
    min_sentence_len: int = 8        # 短于该长度的残句并入下一句
    max_sentence_len: int = 80       # 长句按逗号二次切分
    fixed_window: int = 20           # C 臂固定窗口字符数
    pause_threshold_s: float = 0.15  # 句间隔超过该值计一次停顿

    @classmethod
    def from_env(cls) -> "Settings":
        cfg = load_env()
        return cls(
            base_url=cfg.get("OPENAI_BASE_URL", "http://127.0.0.1:3425/v1"),
            api_key=cfg.get("OPENAI_API_KEY", "magpie"),
            model=cfg.get("OPENAI_MODEL", ""),
        )


if __name__ == "__main__":
    s = Settings.from_env()
    print(f"base_url={s.base_url} model={s.model}")
