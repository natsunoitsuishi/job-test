from __future__ import annotations

from typing import Optional

from config.schema import AppConfig
from devices.common import EventSink
from infra.util.clock import SYSTEM_CLOCK, Clock

from .base import BaseCameraClient, CameraError
from .opt_client import OptCameraClient, PayloadFramer
from .service import CameraService
from .simulator import SimulatedCameraClient


def create_camera_service(cfg: AppConfig, emit: EventSink, clock: Clock = SYSTEM_CLOCK, *,
                          simulate: bool = False, bind_host: Optional[str] = None) -> CameraService:
    """按 cfg.camera.enabled 创建客户端；相机侧走 ETH0，bind_host 缺省取 network.eth0_bind。"""
    bind = cfg.network.eth0_bind if bind_host is None else bind_host
    cls = SimulatedCameraClient if simulate else OptCameraClient
    return CameraService([cls(c, emit, clock, bind) for c in cfg.camera.enabled])


__all__ = ["BaseCameraClient", "CameraError", "CameraService", "OptCameraClient", "PayloadFramer",
           "SimulatedCameraClient", "create_camera_service"]
