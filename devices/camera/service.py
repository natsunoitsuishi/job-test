"""CameraService：把多台相机聚合成运行时使用的单一入口。"""
from __future__ import annotations

import asyncio
from typing import Sequence

from domain.models import DeviceHealth
from infra.logging import get_logger

from .base import BaseCameraClient, CameraError

logger = get_logger("camera")


class CameraService:
    def __init__(self, clients: Sequence[BaseCameraClient]) -> None:
        self._clients: dict[str, BaseCameraClient] = {c.id: c for c in clients}

    @property
    def clients(self) -> list[BaseCameraClient]:
        return list(self._clients.values())

    def get(self, camera_id: str) -> BaseCameraClient:
        return self._clients[camera_id]

    @property
    def camera_ids(self) -> list[str]:
        return list(self._clients)

    def online_ids(self) -> list[str]:
        return [c.id for c in self._clients.values() if c.online]

    async def start(self) -> None:
        for c in self._clients.values():
            await c.start()

    async def stop(self) -> None:
        await self.stop_scan_session()
        await asyncio.gather(*(c.stop() for c in self._clients.values()), return_exceptions=True)

    async def wait_ready(self, timeout_s: float) -> list[str]:
        """等待所有相机上线，返回超时仍离线的相机 id（空列表表示全部在线）。"""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout_s
        while True:
            offline = [c.id for c in self._clients.values() if not c.online]
            if not offline or loop.time() >= end:
                return offline
            await asyncio.sleep(0.05)

    async def start_scan_session(self) -> None:
        """至少一台启动成功即视为成功；离线的相机在重连后自动补发 start。全部失败则抛 CameraError。"""
        cams = self.clients
        results = await asyncio.gather(*(c.start_scan_session() for c in cams), return_exceptions=True)
        failed = [(c.id, r) for c, r in zip(cams, results) if isinstance(r, BaseException)]
        for cid, err in failed:
            logger.warning("相机 %s 启动会话失败: %s", cid, err, extra={"camera_id": cid})
        if len(failed) == len(cams):
            await self.stop_scan_session()      # 清除 session_wanted，避免重连后空转
            raise CameraError("所有相机启动失败: " + "; ".join(f"{cid}: {err}" for cid, err in failed))

    async def stop_scan_session(self) -> None:
        cams = self.clients
        results = await asyncio.gather(*(c.stop_scan_session() for c in cams), return_exceptions=True)
        for c, r in zip(cams, results):
            if isinstance(r, BaseException):
                logger.warning("相机 %s 停止会话异常: %s", c.id, r, extra={"camera_id": c.id})

    async def trigger_once(self, camera_id: str, track_id: str) -> None:
        await self._clients[camera_id].trigger_once(track_id)

    def get_healths(self) -> list[DeviceHealth]:
        return [c.get_health() for c in self._clients.values()]

    def snapshot(self) -> list[dict]:
        return [c.snapshot() for c in self._clients.values()]
