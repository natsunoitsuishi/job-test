"""PLC 接口（README §11.17）+ 摆轮分拣扩展。

摆轮分拣是旧代码中 RuntimeService._plc_handle_trigger 的业务，README 范围之外，作为可选扩展保留：
- 码值 → 道口：int(code) % lane_count，余 0 记为最后一道（与旧代码一致）；非 OK 或非数字码 → reject_lane；
- 道口 1：在 delays_ms.d0 后写 D0=1；
- 其他道口：d0 后写 D0=lane，d1 后写 D1=lane；
- dry_run=true 时只打日志（旧代码 to_plc 处于注释状态，即实际是 dry run）。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from config.schema import DivertSection
from devices.modbus_util import call_unit
from domain.enums import DecisionStatus


class PlcClient(ABC):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def notify_ready(self, value: bool) -> None: ...

    @abstractmethod
    async def notify_scan_result(self, status: DecisionStatus, track_id: str, code: Optional[str]) -> None: ...

    async def notify_reject(self, track_id: str) -> None: ...


def code_to_lane(code: Optional[str], status: DecisionStatus, lane_count: int, reject_lane: int) -> int:
    if status is not DecisionStatus.OK or not code:
        return reject_lane
    try:
        r = int(code) % lane_count
    except ValueError:
        return reject_lane
    return r if r != 0 else lane_count


class DivertPlcClient(PlcClient):
    def __init__(self, cfg: DivertSection) -> None:
        self.cfg = cfg
        self.logger = logging.getLogger("vg.report.divert")
        self._client = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.history: list[tuple[str, int, int]] = []   # (track_id, register, value)，便于测试/追溯

    async def connect(self) -> None:
        if self.cfg.dry_run:
            self.logger.info("摆轮分拣 dry_run 模式，不连接 PLC")
            return
        await self._ensure_client()

    async def _ensure_client(self) -> None:
        from pymodbus.client import AsyncModbusTcpClient

        if self._client is not None and self._client.connected:
            return
        self._client = AsyncModbusTcpClient(host=self.cfg.host, port=self.cfg.port, timeout=self.cfg.timeout_s)
        if not await self._client.connect():
            raise ConnectionError(f"PLC 连接失败 {self.cfg.host}:{self.cfg.port}")

    async def disconnect(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client is not None:
            self._client.close()
            self._client = None

    async def notify_scan_result(self, status: DecisionStatus, track_id: str, code: Optional[str]) -> None:
        lane = code_to_lane(code, status, self.cfg.lane_count, self.cfg.reject_lane)
        plan = [("d0", self.cfg.delays_ms["d0"])]
        if lane != 1:
            plan.append(("d1", self.cfg.delays_ms["d1"]))
        self.logger.info("track_id=%s | 分拣 code=%s status=%s -> 道口 %d", track_id, code, status.value, lane)
        for reg_name, delay in plan:
            t = asyncio.ensure_future(self._write_later(track_id, self.cfg.registers[reg_name], lane, delay))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _write_later(self, track_id: str, register: int, value: int, delay_ms: int) -> None:
        await asyncio.sleep(delay_ms / 1000)
        self.history.append((track_id, register, value))
        if self.cfg.dry_run:
            self.logger.info("track_id=%s | [dry_run] 写寄存器 D%d=%d", track_id, register, value)
            return
        try:
            async with self._lock:
                await self._ensure_client()
                rr = await call_unit(self._client.write_register, self.cfg.unit_id, address=register, value=value)
            if rr.isError():
                raise IOError(str(rr))
            self.logger.info("track_id=%s | 写寄存器 D%d=%d", track_id, register, value)
        except Exception as e:
            self.logger.error("track_id=%s | 写寄存器 D%d 失败: %s", track_id, register, e)
            if self._client is not None:
                self._client.close()
                self._client = None