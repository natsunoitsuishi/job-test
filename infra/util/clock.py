"""时钟抽象：生产用系统时钟，测试/仿真用可手动推进的时钟。"""
from __future__ import annotations

import time
from datetime import datetime


class Clock:
    def now(self) -> float:
        return time.time()


class ManualClock(Clock):
    def __init__(self, start: float = 1_000_000.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> float:
        self._t += seconds
        return self._t

    def set(self, ts: float) -> None:
        self._t = ts


SYSTEM_CLOCK = Clock()


def ms(seconds: float) -> float:
    return seconds * 1000.0


def sec(milliseconds: float) -> float:
    return milliseconds / 1000.0


def fmt_ts(ts: float) -> str:
    """Unix 秒 → 'YYYY-MM-DD HH:MM:SS.mmm'（本地时间）。"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def iso_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")


def make_track_id(ts: float, seq: int = 0) -> str:
    """T + 年月日时分秒毫秒（README §11.16 示例：T20260318151201231）；同毫秒冲突追加序号。"""
    base = "T" + datetime.fromtimestamp(ts).strftime("%Y%m%d%H%M%S") + f"{int(ts * 1000) % 1000:03d}"
    return base if seq == 0 else f"{base}-{seq}"

