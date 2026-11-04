"""报警服务（README §9.3 / §11.9 AlarmService）：统一报警码、级别、恢复，并广播给 UI/DB/外部接口。"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable, Optional, Union

from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

from .enums import AlarmCode, AlarmLevel

logger = get_logger("alarm")

CodeLike = Union[AlarmCode, str]
LevelLike = Union[AlarmLevel, str]


def _code_str(code: CodeLike) -> str:
    return code.value if isinstance(code, AlarmCode) else str(code)


def _level_str(level: LevelLike) -> str:
    return level.value if isinstance(level, AlarmLevel) else str(level).upper()


@dataclass
class Alarm:
    code: str
    message: str
    level: str
    start_ts: float
    key: str = ""
    last_ts: float = 0.0
    end_ts: Optional[float] = None
    ack_user: Optional[str] = None
    count: int = 1

    @property
    def active(self) -> bool:
        return self.end_ts is None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["alarm_code"] = self.code
        d["alarm_level"] = self.level
        return d


# listener(event, alarm)；event ∈ {"raised", "updated", "cleared", "acked"}
AlarmListener = Callable[[str, Alarm], None]


class AlarmService:
    def __init__(self, clock: Clock = SYSTEM_CLOCK, history_size: int = 500) -> None:
        self._clock = clock
        self._active: dict[tuple[str, str], Alarm] = {}
        self._history: deque[Alarm] = deque(maxlen=history_size)
        self._last_raised: dict[tuple[str, str], float] = {}
        self._listeners: list[AlarmListener] = []

    # ------------------------------------------------------------------ 订阅
    def add_listener(self, cb: AlarmListener) -> None:
        self._listeners.append(cb)

    def _notify(self, event: str, alarm: Alarm) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, alarm)
            except Exception:  # noqa: BLE001 监听者异常不得影响主流程
                logger.exception("报警监听回调异常")

    # ------------------------------------------------------------------ 触发/恢复
    def raise_alarm(self, code: CodeLike, message: str, level: LevelLike = "ERROR", *,
                    key: str = "", suppress_ms: int = 0, ts: Optional[float] = None) -> Optional[Alarm]:
        """同 (code, key) 已激活则累加计数；suppress_ms>0 时在抑制期内的重复触发被忽略（返回 None）。"""
        now = self._clock.now() if ts is None else ts
        c = _code_str(code)
        k = (c, key)
        existing = self._active.get(k)
        if existing is not None:
            existing.count += 1
            existing.last_ts = now
            existing.message = message
            self._last_raised[k] = now
            self._notify("updated", existing)
            return existing
        last = self._last_raised.get(k)
        if suppress_ms > 0 and last is not None and (now - last) * 1000.0 < suppress_ms:
            return None
        alarm = Alarm(code=c, message=message, level=_level_str(level), start_ts=now, key=key, last_ts=now)
        self._active[k] = alarm
        self._last_raised[k] = now
        log = {"INFO": logger.info, "WARN": logger.warning}.get(alarm.level, logger.error)
        log("ALARM %s%s: %s", c, f"[{key}]" if key else "", message)
        self._notify("raised", alarm)
        return alarm

    def clear_alarm(self, code: CodeLike, key: str = "", ts: Optional[float] = None) -> Optional[Alarm]:
        k = (_code_str(code), key)
        alarm = self._active.pop(k, None)
        if alarm is None:
            return None
        alarm.end_ts = self._clock.now() if ts is None else ts
        self._history.append(alarm)
        logger.info("ALARM 恢复 %s%s", alarm.code, f"[{key}]" if key else "")
        self._notify("cleared", alarm)
        return alarm

    def clear_code(self, code: CodeLike, ts: Optional[float] = None) -> list[Alarm]:
        """清除某报警码下所有 key。"""
        c = _code_str(code)
        return [a for a in (self.clear_alarm(c, k, ts) for (cc, k) in list(self._active) if cc == c) if a]

    def clear_all(self) -> None:
        for (c, k) in list(self._active):
            self.clear_alarm(c, k)

    def ack(self, code: CodeLike, user: str, key: str = "") -> Optional[Alarm]:
        alarm = self._active.get((_code_str(code), key))
        if alarm is None:
            for a in reversed(self._history):
                if a.code == _code_str(code) and a.key == key and a.ack_user is None:
                    alarm = a
                    break
        if alarm is None:
            return None
        alarm.ack_user = user
        self._notify("acked", alarm)
        return alarm

    # ------------------------------------------------------------------ 查询
    def list_active_alarms(self) -> list[Alarm]:
        return sorted(self._active.values(), key=lambda a: a.start_ts)

    def list_history(self, limit: int = 100) -> list[Alarm]:
        return list(self._history)[-limit:]

    def has_active(self, code: CodeLike, key: Optional[str] = None) -> bool:
        c = _code_str(code)
        return any(cc == c and (key is None or kk == key) for (cc, kk) in self._active)

    def active_codes(self) -> set[str]:
        return {c for (c, _) in self._active}

    def current_summary(self) -> str:
        """UI 主窗口显示的当前报警（最新一条）。"""
        active = self.list_active_alarms()
        return f"{active[-1].code}: {active[-1].message}" if active else ""

