"""领域模型（README §7.3 BoxTrack / §11.7 / §11.8）。

约定：所有时间戳 ts 为 Unix 秒（float，time.time() 同基准）；所有毫秒量以 *_ms 命名。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .enums import DecisionStatus, DeviceStatus, EventType, RunMode, TrackStatus


@dataclass
class CameraTriggerPlan:
    """burst_trigger 降级模式下，单次触发计划。"""
    camera_id: str
    trigger_ts: float
    trigger_offset_mm: float
    trigger_sent: bool = False


@dataclass
class CameraResult:
    camera_id: str
    code: Optional[str]
    raw_payload: dict
    result_ts: float
    success: bool
    symbology: str = ""


@dataclass
class BoxTrack:
    track_id: str
    mode: RunMode
    created_ts: float
    pe1_on_ts: Optional[float] = None
    pe1_off_ts: Optional[float] = None
    pe2_on_ts: Optional[float] = None
    pe2_off_ts: Optional[float] = None
    speed_mm_s: Optional[float] = None
    length_mm: Optional[float] = None
    scan_window_start_ts: Optional[float] = None
    scan_window_end_ts: Optional[float] = None
    first_ok_ts: Optional[float] = None
    scan_close_reason: Optional[str] = None
    status: TrackStatus = TrackStatus.CREATED
    trigger_plans: list[CameraTriggerPlan] = field(default_factory=list)
    camera_results: list[CameraResult] = field(default_factory=list)
    final_code: Optional[str] = None
    final_status: Optional[DecisionStatus] = None
    alarm_codes: list[str] = field(default_factory=list)
    finalized_ts: Optional[float] = None
    gap_violation: bool = False

    # ---- 窗口辅助 ----
    @property
    def window_open(self) -> bool:
        return self.status is TrackStatus.WINDOW_OPEN

    @property
    def window_started(self) -> bool:
        return self.scan_window_start_ts is not None

    def window_contains(self, ts: float, tolerance_s: float = 0.0) -> bool:
        """ts 是否落在 [start - tol, end + tol]；窗口未关时 end 视为 +inf。"""
        if self.scan_window_start_ts is None:
            return False
        if ts < self.scan_window_start_ts - tolerance_s:
            return False
        if self.scan_window_end_ts is None:
            return True
        return ts <= self.scan_window_end_ts + tolerance_s

    def window_center(self, now_ts: float) -> float:
        start = self.scan_window_start_ts if self.scan_window_start_ts is not None else self.created_ts
        end = self.scan_window_end_ts if self.scan_window_end_ts is not None else now_ts
        return (start + end) / 2.0

    def add_alarm(self, code: str) -> None:
        if code not in self.alarm_codes:
            self.alarm_codes.append(code)

    @property
    def valid_results(self) -> list[CameraResult]:
        return [r for r in self.camera_results if r.success and r.code]


@dataclass
class DeviceHealth:
    device_id: str
    device_type: str
    status: DeviceStatus
    last_heartbeat_ts: Optional[float] = None
    message: str = ""


@dataclass
class AppEvent:
    event_id: str
    event_type: EventType
    source: str
    ts: float
    payload: dict[str, Any] = field(default_factory=dict)
