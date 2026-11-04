"""Modbus-TCP 远程 IO 驱动（不依赖 pymodbus）。

假设：DI 地址 = 通道号（从 0 起），用功能码 02 读；设备返回「非法功能」时自动改用 01。
DO 用功能码 05 写单线圈，地址 = 通道号。
"""
from __future__ import annotations

import asyncio
import contextlib
import struct
from typing import Optional

from config.schema import DioSection
from devices.common import EventSink, tune_tcp_socket
from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

from .base import BaseDiDoService, DioError

logger = get_logger("dio")


class ModbusError(DioError):
    def __init__(self, msg: str, code: Optional[int] = None) -> None:
        super().__init__(msg)
        self.code = code


class ModbusTcpClient:
    def __init__(self, host: str, port: int, unit_id: int = 1, timeout_s: float = 3.0,
                 bind_host: str = "") -> None:
        self.host, self.port, self.unit_id = host, port, unit_id
        self.timeout_s = timeout_s
        self.bind_host = bind_host
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._tid = 0
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def connect(self) -> None:
        await self.close()
        local = (self.bind_host, 0) if self.bind_host else None
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, local_addr=local), self.timeout_s)
        tune_tcp_socket(self._writer)

    async def close(self) -> None:
        w, self._reader, self._writer = self._writer, None, None
        if w is not None:
            w.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(w.wait_closed(), 1.0)

    async def _request(self, fc: int, data: bytes) -> bytes:
        if self._writer is None or self._reader is None:
            raise ConnectionError("Modbus 未连接")
        async with self._lock:
            self._tid = (self._tid + 1) & 0xFFFF
            pdu = bytes([fc]) + data
            frame = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit_id) + pdu
            try:
                self._writer.write(frame)
                await self._writer.drain()
                header = await asyncio.wait_for(self._reader.readexactly(7), self.timeout_s)
                tid, _pid, length, _unit = struct.unpack(">HHHB", header)
                body = await asyncio.wait_for(self._reader.readexactly(max(length - 1, 0)), self.timeout_s)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, OSError) as e:
                await self.close()
                raise ConnectionError(f"Modbus 通讯失败: {type(e).__name__}") from e
            if tid != self._tid or not body:
                await self.close()
                raise ConnectionError("Modbus 响应错位")
            if body[0] == (fc | 0x80):
                code = body[1] if len(body) > 1 else None
                raise ModbusError(f"Modbus 异常响应 fc={fc} code={code}", code)
            if body[0] != fc:
                await self.close()
                raise ConnectionError("Modbus 功能码不匹配")
            return body

    async def read_bits(self, fc: int, addr: int, count: int) -> list[bool]:
        body = await self._request(fc, struct.pack(">HH", addr, count))
        need = (count + 7) // 8
        if len(body) < 2 + need or body[1] < need:
            raise ConnectionError("Modbus 读响应长度异常")
        bits = body[2:2 + need]
        return [bool((bits[i // 8] >> (i % 8)) & 1) for i in range(count)]

    async def write_coil(self, addr: int, value: bool) -> None:
        await self._request(0x05, struct.pack(">HH", addr, 0xFF00 if value else 0x0000))


class ModbusDiDoService(BaseDiDoService):
    def __init__(self, cfg: DioSection, emit: EventSink, clock: Clock = SYSTEM_CLOCK, *,
                 bind_host: str = "", client: Optional[ModbusTcpClient] = None) -> None:
        super().__init__(cfg, emit, clock)
        self._client = client or ModbusTcpClient(cfg.host, cfg.port, cfg.unit_id, cfg.timeout_s, bind_host)
        self._di_count = max(cfg.di_map.values()) + 1
        self._di_fc = 2

    async def _open(self) -> None:
        await self._client.connect()
        for ch in self.cfg.do_map.values():       # 上线先把所有输出复位，避免残留状态
            await self._client.write_coil(ch, False)
            self.do_state[ch] = False

    async def _close(self) -> None:
        await self._client.close()

    async def _read_inputs(self) -> dict[int, bool]:
        try:
            bits = await self._client.read_bits(self._di_fc, 0, self._di_count)
        except ModbusError as e:
            if e.code == 1 and self._di_fc == 2:
                logger.warning("设备不支持功能码02，改用01读取输入")
                self._di_fc = 1
                bits = await self._client.read_bits(1, 0, self._di_count)
            else:
                raise
        return dict(enumerate(bits))

    async def _write_output(self, channel: int, value: bool) -> None:
        await self._client.write_coil(channel, value)
