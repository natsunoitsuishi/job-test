"""领域枚举（README §7.2 / §11.7 / §11.8 / §11.10）。"""
from __future__ import annotations

from enum import Enum


class RunMode(str, Enum):
    LR = "LR"   # 左右扫：码在箱子左/右侧面
    FB = "FB"   # 前后扫：码在箱子前/后端面


class TrackStatus(str, Enum):
    CREATED = "CREATED"                # PE1 上升沿已建轨
    TRACKING = "TRACKING"              # PE2 已匹配，测得速度
    WINDOW_OPEN = "WINDOW_OPEN"        # 读码窗口打开，接受结果
    WAITING_RESULT = "WAITING_RESULT"  # 窗口已关，等待迟到结果（grace）
    FINALIZED = "FINALIZED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in (TrackStatus.FINALIZED, TrackStatus.EXPIRED)


class DecisionStatus(str, Enum):
    OK = "OK"
    NO_READ = "NO_READ"
    AMBIGUOUS = "AMBIGUOUS"
    TIMEOUT = "TIMEOUT"
    FAULT = "FAULT"

    @property
    def is_ok(self) -> bool:
        return self is DecisionStatus.OK


class DeviceStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"


class EventType(str, Enum):
    PE_RISE = "PE_RISE"
    PE_FALL = "PE_FALL"
    CAMERA_RESULT = "CAMERA_RESULT"
    CAMERA_HEARTBEAT = "CAMERA_HEARTBEAT"
    TIMER_TRIGGER = "TIMER_TRIGGER"
    TRACK_TIMEOUT = "TRACK_TIMEOUT"
    DEVICE_FAULT = "DEVICE_FAULT"
    DEVICE_RECOVER = "DEVICE_RECOVER"
    OPERATOR_CMD = "OPERATOR_CMD"


class SystemState(str, Enum):
    BOOT = "BOOT"
    LOAD_CONFIG = "LOAD_CONFIG"
    INIT_DEVICES = "INIT_DEVICES"
    SELF_CHECK = "SELF_CHECK"
    READY = "READY"
    RUNNING = "RUNNING"
    FAULT = "FAULT"
    STOPPING = "STOPPING"


class AlarmLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class AlarmCode(str, Enum):
    # README §7.2 异常分支
    CAM_TIMEOUT = "CAM_TIMEOUT"
    NO_READ = "NO_READ"
    AMBIGUOUS = "AMBIGUOUS"
    SENSOR_SEQUENCE_ERROR = "SENSOR_SEQUENCE_ERROR"
    TRACK_OVERLAP = "TRACK_OVERLAP"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    UNBOUND_RESULT = "UNBOUND_RESULT"
    # README §9.3 报警与联锁
    CONSECUTIVE_NO_READ = "CONSECUTIVE_NO_READ"
    PE_STUCK = "PE_STUCK"
    REJECT_FAIL = "REJECT_FAIL"
    # README §11.18 异常处理
    DB_ERROR = "DB_ERROR"
    REPORT_FAIL = "REPORT_FAIL"
    CONFIG_ERROR = "CONFIG_ERROR"
