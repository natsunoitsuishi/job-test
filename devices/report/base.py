"""上报接口抽象（README §11.17）。返回 bool 表示是否送达；缓存与重试由服务层负责。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from domain.enums import DecisionStatus


class SchedulerClient(ABC):
    enabled: bool = True

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def report_result(self, payload: dict) -> bool: ...

    @abstractmethod
    async def report_heartbeat(self, payload: dict) -> bool: ...


class MesClient(ABC):
    enabled: bool = True

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def report_scan_record(self, payload: dict) -> bool: ...


class PlcClient(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def notify_ready(self, value: bool) -> None: ...

    @abstractmethod
    async def notify_scan_result(self, result: DecisionStatus, track_id: str) -> None: ...

    @abstractmethod
    async def notify_reject(self, track_id: str) -> None: ...
