"""配置结构定义与校验（README §11.14 / §11.18 "配置错误阻断启动"）。"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.enums import RunMode


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppSection(_Section):
    site_name: str = "Vision Gate"
    device_id: str = "VG-01"
    line_id: str = "LINE-01"
    debug: bool = False


class RuntimeSection(_Section):
    mode: RunMode = RunMode.LR
    line_speed_mm_s: float = Field(800, gt=0)
    speed_tolerance: float = Field(0.4, gt=0, lt=1)
    track_ttl_ms: int = Field(1500, gt=0)
    tick_interval_ms: int = Field(10, ge=1, le=100)
    self_check_timeout_s: float = Field(5, ge=0)


class TriggerSection(_Section):
    scan_mode: Literal["continuous_window", "burst_trigger"] = "continuous_window"
    sensor_distance_mm: float = Field(120, gt=0)
    default_box_length_mm: float = Field(350, gt=0)
    read_zone_margin_mm: float = Field(50, ge=0)
    window_lead_ms: int = Field(150, ge=0)
    window_tolerance_ms: int = Field(30, ge=0)
    result_grace_ms: int = Field(60, ge=0)
    trigger_timeout_ms: int = Field(300, gt=0)
    camera_result_timeout_ms: int = Field(500, gt=0)
    min_box_gap_mm: float = Field(50, ge=0)
    gap_violation_policy: Literal["ng", "alarm_only"] = "ng"
    tail_delay_ms: int = Field(80, ge=0)
    idle_off_delay_ms: int = Field(150, ge=0)
    track_hold_after_ok_ms: int = Field(50, ge=0)
    max_track_window_ms: int = Field(400, gt=0)
    allow_single_camera_ok: bool = True
    ambiguity_policy: Literal["ng", "first"] = "ng"
    burst_count: int = Field(4, ge=1, le=10)
    burst_interval_ms: int = Field(40, ge=1)
    consecutive_no_read_alarm: int = Field(3, ge=1)
    unbound_alarm_suppress_ms: int = Field(1500, ge=0)


class NetworkSection(_Section):
    eth0_bind: str = ""
    eth1_bind: str = ""


class CameraConfig(_Section):
    id: str
    enabled: bool = True
    host: str
    read_port: int = Field(1024, gt=0, lt=65536)
    trigger_port: int = Field(1025, gt=0, lt=65536)
    pe2_offset_mm: float = Field(100, ge=0)
    result_latency_ms: float = Field(0, ge=0)
    payload_format: Literal["json", "text"] = "json"
    start_cmd: str = "start"
    stop_cmd: str = "stop"
    trigger_cmd: str = "start"
    connect_timeout_s: float = Field(3.0, gt=0)
    reconnect_interval_s: float = Field(2.0, gt=0)
    heartbeat_timeout_s: float = Field(10.0, gt=0)


class CameraSection(_Section):
    camera_a: CameraConfig
    camera_b: Optional[CameraConfig] = None

    @property
    def all(self) -> list[CameraConfig]:
        return [c for c in (self.camera_a, self.camera_b) if c is not None]

    @property
    def enabled(self) -> list[CameraConfig]:
        return [c for c in self.all if c.enabled]

    @model_validator(mode="after")
    def _check(self) -> "CameraSection":
        ids = [c.id for c in self.all]
        if len(ids) != len(set(ids)):
            raise ValueError(f"相机 id 重复: {ids}")
        if not self.enabled:
            raise ValueError("至少需要启用一台相机")
        return self


class DioSection(_Section):
    # modbus: 远程 Modbus-TCP IO 模块；rk3588: IPC 本机 DI/DO（sysfs GPIO）；simulator: 仿真
    driver: Literal["modbus", "rk3588", "simulator"] = "modbus"
    host: str = "127.0.0.1"
    port: int = Field(502, gt=0, lt=65536)
    unit_id: int = Field(1, ge=0, le=247)
    timeout_s: float = Field(3.0, gt=0)
    poll_interval_ms: int = Field(5, ge=1, le=100)
    debounce_ms: int = Field(4, ge=0)
    pe_stuck_on_ms: int = Field(5000, gt=0)
    reconnect_interval_s: float = Field(2.0, gt=0)
    gpio_di_pins: List[int] = Field(default_factory=list)   # rk3588：按通道号索引的 GPIO 编号
    gpio_do_pins: List[int] = Field(default_factory=list)
    di_map: Dict[str, int] = Field(default_factory=lambda: {"pe1": 0, "pe2": 1})
    do_map: Dict[str, int] = Field(default_factory=lambda: {"ok": 0, "ng": 1, "reject": 2})
    pulse_ms: Dict[str, int] = Field(default_factory=lambda: {"ok": 80, "ng": 120, "reject": 200})

    @field_validator("di_map", "do_map")
    @classmethod
    def _no_channel_conflict(cls, v: Dict[str, int]) -> Dict[str, int]:
        chans = list(v.values())
        if len(chans) != len(set(chans)):
            raise ValueError(f"通道冲突: {v}")
        if any(c < 0 for c in chans):
            raise ValueError(f"通道号不能为负: {v}")
        return v

    @model_validator(mode="after")
    def _check(self) -> "DioSection":
        for name in ("pe1", "pe2"):
            if name not in self.di_map:
                raise ValueError(f"di_map 缺少 {name}")
        for name in ("ok", "ng"):
            if name not in self.do_map:
                raise ValueError(f"do_map 缺少 {name}")
        for name in self.pulse_ms:
            if name not in self.do_map:
                raise ValueError(f"pulse_ms 含未映射的输出 {name}")
        if self.driver == "rk3588":
            if self.di_map and len(self.gpio_di_pins) <= max(self.di_map.values()):
                raise ValueError("rk3588 驱动需要为每个 di 通道配置 gpio_di_pins")
            if len(self.gpio_do_pins) <= max(self.do_map.values()):
                raise ValueError("rk3588 驱动需要为每个 do 通道配置 gpio_do_pins")
        return self


class SchedulerReportConfig(_Section):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(8080, gt=0, lt=65536)
    heartbeat_interval_s: float = Field(5, gt=0)


class MesReportConfig(_Section):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(9090, gt=0, lt=65536)


class ReportSection(_Section):
    scheduler: SchedulerReportConfig = Field(default_factory=SchedulerReportConfig)
    mes: MesReportConfig = Field(default_factory=MesReportConfig)
    retry_interval_s: float = Field(5, gt=0)
    max_cache: int = Field(1000, ge=1)


class StorageSection(_Section):
    db_path: str = "data/app.db"
    log_dir: str = "logs"
    export_dir: str = "exports"
    archive_dir: str = "data/archive"
    archive_raw_payload: bool = True


class LoggingSection(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    console: bool = True


class AppConfig(_Section):
    app: AppSection = Field(default_factory=AppSection)
    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    trigger: TriggerSection = Field(default_factory=TriggerSection)
    network: NetworkSection = Field(default_factory=NetworkSection)
    camera: CameraSection
    dio: DioSection = Field(default_factory=DioSection)
    report: ReportSection = Field(default_factory=ReportSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)

    @model_validator(mode="after")
    def _cross_check(self) -> "AppConfig":
        t = self.trigger
        if t.max_track_window_ms < t.track_hold_after_ok_ms:
            raise ValueError("max_track_window_ms 不能小于 track_hold_after_ok_ms")
        if self.runtime.track_ttl_ms < t.max_track_window_ms + t.result_grace_ms:
            raise ValueError("track_ttl_ms 必须 >= max_track_window_ms + result_grace_ms")
        return self
