"""事件工厂（README §11.8）。"""
from __future__ import annotations

import itertools
import time
from typing import Any, Optional

from .enums import EventType
from .models import AppEvent

_seq = itertools.count(1)


def make_event(event_type: EventType, source: str, payload: Optional[dict[str, Any]] = None,
               ts: Optional[float] = None) -> AppEvent:
    n = next(_seq)
    t = time.time() if ts is None else ts
    return AppEvent(event_id=f"E{int(t * 1000)}-{n}", event_type=event_type, source=source,
                    ts=t, payload=payload or {})


def pe_event(sensor: str, state: bool, ts: float) -> AppEvent:
    """sensor: 'PE1' / 'PE2'。"""
    et = EventType.PE_RISE if state else EventType.PE_FALL
    return make_event(et, f"dio:{sensor}", {"sensor": sensor, "state": state}, ts)


def device_fault_event(device_id: str, device_type: str, message: str,
                       ts: Optional[float] = None) -> AppEvent:
    return make_event(EventType.DEVICE_FAULT, device_id,
                      {"device_id": device_id, "device_type": device_type, "message": message}, ts)


def device_recover_event(device_id: str, device_type: str, ts: Optional[float] = None) -> AppEvent:
    return make_event(EventType.DEVICE_RECOVER, device_id,
                      {"device_id": device_id, "device_type": device_type}, ts)

