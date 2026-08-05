from __future__ import annotations


def log(msg: str) -> None:
    """进度日志（借鉴 docs/translate_subtitles.py 的阶段打印风格）。"""
    print(msg, flush=True)
