from __future__ import annotations

from domain.enums import DecisionStatus

from .base import MesClient, PlcClient, SchedulerClient


class NoopSchedulerClient(SchedulerClient):
    enabled = False

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def report_result(self, payload: dict) -> bool: return True
    async def report_heartbeat(self, payload: dict) -> bool: return True


class NoopMesClient(MesClient):
    enabled = False

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def report_scan_record(self, payload: dict) -> bool: return True


class NoopPlcClient(PlcClient):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def notify_ready(self, value: bool) -> None: ...
    async def notify_scan_result(self, result: DecisionStatus, track_id: str) -> None: ...
    async def notify_reject(self, track_id: str) -> None: ...
