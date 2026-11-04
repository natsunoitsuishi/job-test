"""MES 客户端：HTTP/1.1 POST JSON（标准库实现，无额外依赖）。2xx 视为送达。"""
from __future__ import annotations

import asyncio
import contextlib
import json

from config.schema import MesReportConfig
from infra.logging import get_logger

from .base import MesClient

logger = get_logger("report")


class HttpMesClient(MesClient):
    enabled = True

    def __init__(self, cfg: MesReportConfig, bind_host: str = "", timeout_s: float = 3.0,
                 path: str = "/api/scan_record") -> None:
        self.cfg = cfg
        self.bind_host = bind_host
        self.timeout_s = timeout_s
        self.path = path

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def report_scan_record(self, payload: dict) -> bool:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        head = (f"POST {self.path} HTTP/1.1\r\nHost: {self.cfg.host}:{self.cfg.port}\r\n"
                f"Content-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n").encode("ascii")
        local = (self.bind_host, 0) if self.bind_host else None
        w = None
        try:
            r, w = await asyncio.wait_for(
                asyncio.open_connection(self.cfg.host, self.cfg.port, local_addr=local), self.timeout_s)
            w.write(head + body)
            await asyncio.wait_for(w.drain(), self.timeout_s)
            status = await asyncio.wait_for(r.readline(), self.timeout_s)
            parts = status.split()
            ok = len(parts) >= 2 and parts[1].startswith(b"2")
            if not ok:
                logger.warning("MES 返回非 2xx: %r", status[:80])
            return ok
        except (OSError, asyncio.TimeoutError) as e:
            logger.warning("MES 上报失败: %s", e)
            return False
        finally:
            if w is not None:
                w.close()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(w.wait_closed(), 0.5)
