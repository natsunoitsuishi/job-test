"""调度上位机客户端（ETH1）：TCP 长连接，一行一个 JSON。
假设协议：{"type": "result"|"heartbeat", "data": {...}}\n，无应答；写成功即视为送达。"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Optional

from config.schema import SchedulerReportConfig
from devices.common import tune_tcp_socket
from infra.logging import get_logger

from .base import SchedulerClient

logger = get_logger("report")


class TcpSchedulerClient(SchedulerClient):
    enabled = True

    def __init__(self, cfg: SchedulerReportConfig, bind_host: str = "", timeout_s: float = 2.0) -> None:
        self.cfg = cfg
        self.bind_host = bind_host
        self.timeout_s = timeout_s
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        async with self._lock:
            await self._ensure()

    async def _ensure(self) -> None:
        if self._writer is not None and not self._writer.is_closing():
            return
        local = (self.bind_host, 0) if self.bind_host else None
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.cfg.host, self.cfg.port, local_addr=local), self.timeout_s)
        tune_tcp_socket(self._writer)
        logger.info("调度上位机已连接 %s:%s", self.cfg.host, self.cfg.port)

    async def _drop(self) -> None:
        w, self._reader, self._writer = self._writer, None, None
        if w is not None:
            w.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(w.wait_closed(), 0.5)

    async def disconnect(self) -> None:
        async with self._lock:
            await self._drop()

    async def _send(self, kind: str, payload: dict) -> bool:
        line = (json.dumps({"type": kind, "data": payload}, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        async with self._lock:
            try:
                await self._ensure()
                assert self._writer is not None
                self._writer.write(line)
                await asyncio.wait_for(self._writer.drain(), self.timeout_s)
                return True
            except (OSError, asyncio.TimeoutError, AssertionError) as e:
                logger.warning("调度上报失败(%s): %s", kind, e)
                await self._drop()
                return False

    async def report_result(self, payload: dict) -> bool:
        return await self._send("result", payload)

    async def report_heartbeat(self, payload: dict) -> bool:
        return await self._send("heartbeat", payload)
