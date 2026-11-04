"""判定引擎（README §11.9 DecisionEngine 判定表）。

| 两相机一致 → OK | 仅一台成功 → OK(allow_single_camera_ok) | 都失败 → NO_READ
| 结果冲突 → AMBIGUOUS | 超时无返回 → TIMEOUT | 设备故障无法判定 → FAULT
箱距过小且 gap_violation_policy=ng：归属不可靠，OK 降级为 AMBIGUOUS（README §7.6 视为 NG）。
"""
from __future__ import annotations

from typing import Optional

from config.schema import AppConfig

from .binder import ResultBinder
from .enums import AlarmCode, DecisionStatus
from .models import BoxTrack

_REJECT_STATUSES = (DecisionStatus.NO_READ, DecisionStatus.AMBIGUOUS, DecisionStatus.TIMEOUT)
_ALARM_FOR_STATUS = {
    DecisionStatus.NO_READ: AlarmCode.NO_READ,
    DecisionStatus.AMBIGUOUS: AlarmCode.AMBIGUOUS,
    DecisionStatus.TIMEOUT: AlarmCode.CAM_TIMEOUT,
}


class DecisionEngine:
    def __init__(self, cfg: AppConfig, binder: Optional[ResultBinder] = None) -> None:
        self._binder = binder or ResultBinder(cfg)
        self._consecutive_no_read = 0
        self.update_config(cfg)

    def update_config(self, cfg: AppConfig) -> None:
        self._t = cfg.trigger
        self._binder.update_config(cfg)

    def evaluate(self, track: BoxTrack) -> DecisionStatus:
        """写入 track.final_code / final_status / alarm_codes 并返回状态。"""
        if AlarmCode.DEVICE_OFFLINE.value in track.alarm_codes and not track.valid_results:
            code, status = None, DecisionStatus.FAULT
        else:
            code, status = self._binder.resolve_final_code(track)
            if status.is_ok and track.gap_violation and self._t.gap_violation_policy == "ng":
                code, status = None, DecisionStatus.AMBIGUOUS
                track.add_alarm(AlarmCode.TRACK_OVERLAP.value)
        alarm = _ALARM_FOR_STATUS.get(status)
        if alarm is not None:
            track.add_alarm(alarm.value)
        track.final_code = code if status.is_ok else None
        track.final_status = status
        return status

    @staticmethod
    def output_signals(status: DecisionStatus) -> list[str]:
        """需要输出的 DO 名称（对应 dio.do_map 的键）：OK→ok；其余→ng，读不到/冲突/超时再加 reject。"""
        if status.is_ok:
            return ["ok"]
        signals = ["ng"]
        if status in _REJECT_STATUSES:
            signals.append("reject")
        return signals

    @property
    def consecutive_no_read(self) -> int:
        return self._consecutive_no_read

    def register_outcome(self, status: DecisionStatus) -> bool:
        """累计连续未读箱数；恰好达到 consecutive_no_read_alarm 时返回 True（触发一次报警）。"""
        if status is DecisionStatus.NO_READ or status is DecisionStatus.TIMEOUT:
            self._consecutive_no_read += 1
            return self._consecutive_no_read == self._t.consecutive_no_read_alarm
        self._consecutive_no_read = 0
        return False


