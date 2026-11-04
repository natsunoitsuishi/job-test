"""PLC 最小信号（README §11.17）经 DO 实现。
OK/NG/REJECT 脉冲已由运行时通过 dio.write_pulse 输出，这里只负责 SYSTEM_READY 电平：
需要时在 dio.do_map 中增加 `ready: <通道>`，未配置则不输出。"""
from __future__ import annotations

from devices.dio.base import BaseDiDoService, DioError
from domain.enums import DecisionStatus
from infra.logging import get_logger

from .base import PlcClient

logger = get_logger("plc")


class DioPlcClient(PlcClient):
    READY_NAME = "ready"

    def __init__(self, dio: BaseDiDoService) -> None:
        self._dio = dio

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def notify_ready(self, value: bool) -> None:
        ch = self._dio.cfg.do_map.get(self.READY_NAME)
        if ch is None:
            return
        try:
            await self._dio.write_do(ch, value)
        except DioError as e:
            logger.error("SYSTEM_READY 输出失败: %s", e)

    async def notify_scan_result(self, result: DecisionStatus, track_id: str) -> None: ...
    async def notify_reject(self, track_id: str) -> None: ...
