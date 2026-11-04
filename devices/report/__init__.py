from __future__ import annotations

from typing import Optional

from config.schema import AppConfig
from devices.dio.base import BaseDiDoService

from .base import MesClient, PlcClient, SchedulerClient
from .mes_client import HttpMesClient
from .noop_client import NoopMesClient, NoopPlcClient, NoopSchedulerClient
from .plc_client import DioPlcClient
from .scheduler_client import TcpSchedulerClient


def create_report_clients(cfg: AppConfig, dio: Optional[BaseDiDoService] = None
                          ) -> tuple[SchedulerClient, MesClient, PlcClient]:
    """上报走 ETH1（network.eth1_bind）；未启用的返回 Noop。"""
    bind = cfg.network.eth1_bind
    sched: SchedulerClient = (TcpSchedulerClient(cfg.report.scheduler, bind)
                              if cfg.report.scheduler.enabled else NoopSchedulerClient())
    mes: MesClient = HttpMesClient(cfg.report.mes, bind) if cfg.report.mes.enabled else NoopMesClient()
    plc: PlcClient = (DioPlcClient(dio) if dio is not None and DioPlcClient.READY_NAME in cfg.dio.do_map
                      else NoopPlcClient())
    return sched, mes, plc


__all__ = ["DioPlcClient", "HttpMesClient", "MesClient", "NoopMesClient", "NoopPlcClient",
           "NoopSchedulerClient", "PlcClient", "SchedulerClient", "TcpSchedulerClient",
           "create_report_clients"]
