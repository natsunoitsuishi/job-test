"""设备层公共件：事件构造、TCP 保活、异常基类。

事件载荷约定：
  PE_RISE/PE_FALL     {"sensor": "PE1"|"PE2", "state": bool}              （domain.events.pe_event）
  CAMERA_RESULT       {"result": CameraResult}
  CAMERA_HEARTBEAT    {"camera_id": str}
  DEVICE_FAULT        {"device_id", "device_type", "message", "alarm_code"}  alarm_code: DEVICE_OFFLINE / PE_STUCK
  DEVICE_RECOVER      同上
"""
from __future__ import annotations

import contextlib
import socket
from typing import Callable, Optional

from domain.enums import AlarmCode, EventType
from domain.events import make_event
from domain.models import AppEvent

EventSink = Callable[[AppEvent], None]


class DeviceError(Exception):
    """设备层通用异常。"""


def fault_event(device_id: str, device_type: str, message: str,
                alarm_code: str = AlarmCode.DEVICE_OFFLINE.value, ts: Optional[float] = None) -> AppEvent:
    return make_event(EventType.DEVICE_FAULT, device_id,
                      {"device_id": device_id, "device_type": device_type,
                       "message": message, "alarm_code": alarm_code}, ts)


def recover_event(device_id: str, device_type: str,
                  alarm_code: str = AlarmCode.DEVICE_OFFLINE.value, ts: Optional[float] = None) -> AppEvent:
    return make_event(EventType.DEVICE_RECOVER, device_id,
                      {"device_id": device_id, "device_type": device_type,
                       "message": "", "alarm_code": alarm_code}, ts)


def tune_tcp_socket(writer) -> None:
    """TCP_NODELAY + 快速保活（约 4 秒发现掉线，满足 FAT「5 秒内报警」）。"""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, val in (("TCP_KEEPIDLE", 2), ("TCP_KEEPINTVL", 1), ("TCP_KEEPCNT", 2)):
            if hasattr(socket, name):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), val)
