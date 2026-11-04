from __future__ import annotations

from config.schema import DioSection
from devices.common import EventSink
from infra.util.clock import SYSTEM_CLOCK, Clock

from .base import BaseDiDoService, DioError
from .modbus_dio import ModbusDiDoService, ModbusError, ModbusTcpClient
from .rk3588_dio import Rk3588DiDoService
from .simulator import SimulatedDiDoService


def create_dio(cfg: DioSection, emit: EventSink, clock: Clock = SYSTEM_CLOCK, *,
               bind_host: str = "", sensor_distance_mm: float = 120.0) -> BaseDiDoService:
    if cfg.driver == "simulator":
        return SimulatedDiDoService(cfg, emit, clock, sensor_distance_mm=sensor_distance_mm)
    if cfg.driver == "rk3588":
        return Rk3588DiDoService(cfg, emit, clock)
    return ModbusDiDoService(cfg, emit, clock, bind_host=bind_host)


__all__ = ["BaseDiDoService", "DioError", "ModbusDiDoService", "ModbusError", "ModbusTcpClient",
           "Rk3588DiDoService", "SimulatedDiDoService", "create_dio"]
