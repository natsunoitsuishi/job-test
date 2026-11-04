"""领域层：模型、枚举、事件。核心服务（track_manager 等）在各自模块中按需导入。"""
from .enums import (AlarmCode, AlarmLevel, DecisionStatus, DeviceStatus, EventType, RunMode,
                    SystemState, TrackStatus)
from .events import device_fault_event, device_recover_event, make_event, pe_event
from .models import (AppEvent, BoxTrack, CameraResult, CameraTriggerPlan, DeviceHealth)

__all__ = [
    "AlarmCode", "AlarmLevel", "DecisionStatus", "DeviceStatus", "EventType", "RunMode",
    "SystemState", "TrackStatus", "AppEvent", "BoxTrack", "CameraResult", "CameraTriggerPlan",
    "DeviceHealth", "device_fault_event", "device_recover_event", "make_event", "pe_event",
]
