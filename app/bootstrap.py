"""依赖装配（README §11.6 启动链）。配置错误抛 ConfigError，阻断启动（README §11.18）。

装配顺序：配置 → 日志 → alarms/bus → dio/camera → repo/archive/report → runtime → health → config_service。
simulate=None 时由 dio.driver == "simulator" 决定；simulate=True 会在内存中强制 DIO 与相机走仿真，不改配置文件。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from config import AppConfig, ConfigManager, resolve_path
from devices.camera import CameraService, create_camera_service
from devices.dio import BaseDiDoService, create_dio
from devices.report import create_report_clients
from domain.alarm_service import AlarmService
from infra.db import ScanRepository
from infra.logging import setup_logging
from infra.util.clock import SYSTEM_CLOCK, Clock
from services import (ArchiveService, ConfigService, EventBus, HealthService, ReportService,
                      RuntimeService)


@dataclass
class AppContainer:
    config_manager: ConfigManager
    cfg: AppConfig
    clock: Clock
    simulate: bool
    bus: EventBus
    alarms: AlarmService
    dio: BaseDiDoService
    camera: CameraService
    repo: ScanRepository
    archive: ArchiveService
    report: ReportService
    runtime: RuntimeService
    health: HealthService
    config_service: ConfigService


def build_app(config_path: Optional[str | os.PathLike[str]] = None, *, simulate: Optional[bool] = None,
              clock: Clock = SYSTEM_CLOCK, environ: Optional[Mapping[str, str]] = None) -> AppContainer:
    manager = ConfigManager(config_path, environ)
    cfg = manager.load()
    setup_logging(cfg.logging.level, resolve_path(cfg.storage.log_dir), cfg.logging.console)

    sim = (cfg.dio.driver == "simulator") if simulate is None else bool(simulate)
    dio_cfg = cfg.dio.model_copy(update={"driver": "simulator"}) if sim else cfg.dio

    alarms = AlarmService(clock)
    bus = EventBus()
    dio = create_dio(dio_cfg, bus.emit, clock, bind_host=cfg.network.eth0_bind,
                     sensor_distance_mm=cfg.trigger.sensor_distance_mm)
    camera = create_camera_service(cfg, bus.emit, clock, simulate=sim)
    repo = ScanRepository(cfg.storage.db_path)
    archive = ArchiveService(cfg.storage, clock)
    scheduler_client, mes_client, plc = create_report_clients(cfg, dio)
    report = ReportService(cfg.report, scheduler_client, mes_client, repo, alarms, clock)
    runtime = RuntimeService(cfg, bus, dio, camera, repo, archive, report, plc=plc, alarms=alarms,
                             clock=clock, config_version=manager.version)
    health = HealthService(cfg, dio, camera, repo, report, alarms, runtime.brief, clock)
    config_service = ConfigService(manager, runtime, health, repo)
    return AppContainer(manager, cfg, clock, sim, bus, alarms, dio, camera, repo, archive, report,
                        runtime, health, config_service)
