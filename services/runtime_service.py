"""RuntimeService（README §11.9 / §11.10 / §11.12 / §11.20）：主业务协调器。

- 所有业务事件（PE、相机结果、设备故障、操作指令、定时 tick）经 EventBus 串行消费，
  只有本类修改 TrackManager 的活动轨迹；
- 终态判定后立即输出 DO，随后把 落库 / 归档 / 上报 交给后台 persist 任务，不阻塞事件处理；
- 本类拥有 repo 的 open/close 与报警的持久化。
"""
from __future__ import annotations

import asyncio
import contextlib
from collections import Counter, deque
from typing import Any, Awaitable, Callable, Optional

from config.manager import config_to_dict
from config.schema import AppConfig
from devices.camera.service import CameraService
from devices.dio.base import BaseDiDoService
from devices.report.base import PlcClient
from domain.alarm_service import AlarmService
from domain.binder import ResultBinder
from domain.decision_engine import DecisionEngine
from domain.enums import AlarmCode, DecisionStatus, EventType, RunMode, SystemState
from domain.events import make_event
from domain.models import AppEvent, BoxTrack, CameraResult
from domain.scan_session import ScanSessionController
from domain.scheduler import TriggerScheduler
from domain.track_manager import TrackManager
from infra.db.repository import DbError, ScanRepository, track_summary
from infra.logging import get_logger, track_extra
from infra.util.clock import SYSTEM_CLOCK, Clock, iso_ts
from services.archive_service import ArchiveService
from services.event_bus import EventBus
from services.report_service import ReportService

logger = get_logger("runtime")

_ALARM_LEVEL = {"NO_READ": "WARN", "AMBIGUOUS": "WARN", "CAM_TIMEOUT": "ERROR",
                "TRACK_OVERLAP": "ERROR", "SENSOR_SEQUENCE_ERROR": "ERROR"}
_RAISED_AT_PE = {AlarmCode.SENSOR_SEQUENCE_ERROR.value, AlarmCode.TRACK_OVERLAP.value}
_CLEAR_ON_OK = (AlarmCode.NO_READ, AlarmCode.AMBIGUOUS, AlarmCode.CAM_TIMEOUT, AlarmCode.CONSECUTIVE_NO_READ)
_AUTO_CLEAR = {AlarmCode.TRACK_OVERLAP.value, AlarmCode.SENSOR_SEQUENCE_ERROR.value,
               AlarmCode.UNBOUND_RESULT.value}
_AUTO_CLEAR_AFTER_S = 5.0
_NOT_RUNNABLE = (SystemState.BOOT, SystemState.LOAD_CONFIG, SystemState.INIT_DEVICES,
                 SystemState.SELF_CHECK, SystemState.STOPPING)

Listener = Callable[[str, Any], None]


class RuntimeService:
    def __init__(self, cfg: AppConfig, bus: EventBus, dio: BaseDiDoService, camera: CameraService,
                 repo: ScanRepository, archive: ArchiveService, report: ReportService, *,
                 plc: Optional[PlcClient] = None, alarms: Optional[AlarmService] = None,
                 clock: Clock = SYSTEM_CLOCK, config_version: int = 1) -> None:
        self.cfg = cfg
        self.bus, self.dio, self.camera = bus, dio, camera
        self.repo, self.archive, self.report, self.plc = repo, archive, report, plc
        self.clock = clock
        self.config_version = config_version
        self.alarms = alarms or AlarmService(clock)
        self.alarms.add_listener(self._on_alarm)

        self.mode: RunMode = cfg.runtime.mode
        self.state = SystemState.BOOT
        self.tracks = TrackManager(cfg)
        self.scheduler = TriggerScheduler(cfg, self.tracks)
        self.binder = ResultBinder(cfg)
        self.decision = DecisionEngine(cfg, self.binder)
        self.session = ScanSessionController(
            camera.start_scan_session, camera.stop_scan_session, lambda: self.tracks.active_count,
            idle_off_delay_ms=cfg.trigger.idle_off_delay_ms,
            trigger_timeout_ms=cfg.trigger.trigger_timeout_ms,
            manage_camera=cfg.trigger.scan_mode == "continuous_window", clock=clock)

        self.counters: Counter[str] = Counter()
        self.recent: deque[dict] = deque(maxlen=50)
        self.last_speed: Optional[float] = None
        self._offline: set[str] = set()
        self._listeners: list[Listener] = []
        self._persist_q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(5000)
        self._tick_pending = False
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()

    # ================================================================== 对外接口
    def add_listener(self, cb: Listener) -> None:
        """cb(kind, data)；kind ∈ track_finalized / state / alarm / mode。回调在事件循环线程内同步执行。"""
        self._listeners.append(cb)

    def _notify(self, kind: str, data: Any) -> None:
        for cb in list(self._listeners):
            try:
                cb(kind, data)
            except Exception:  # noqa: BLE001
                logger.exception("运行时监听回调异常")

    def submit_command(self, cmd: str, **payload: Any) -> None:
        """UI → 运行时：clear_alarms / ack(code,key,user) / set_mode(mode) / reset_counters。"""
        self.bus.publish(make_event(EventType.OPERATOR_CMD, "ui", {"cmd": cmd, **payload},
                                    self.clock.now()))

    def brief(self) -> dict:
        return {"state": self.state.value, "mode": self.mode.value,
                "active_tracks": self.tracks.active_count}

    def snapshot(self) -> dict:
        names = ["total"] + [s.value for s in DecisionStatus]
        return {
            "state": self.state.value, "mode": self.mode.value,
            "session": self.session.snapshot(), "pe": self.dio.pe_state,
            "active_tracks": self.tracks.active_count, "speed_mm_s": self.last_speed,
            "counters": {k: self.counters.get(k, 0) for k in names},
            "recent": list(self.recent)[-10:][::-1],
            "alarm": self.alarms.current_summary(),
            "alarms": [a.to_dict() for a in self.alarms.list_active_alarms()],
            "cameras": self.camera.snapshot(), "dio": self.dio.snapshot(),
            "queue": self.bus.qsize(), "offline": sorted(self._offline),
        }

    def apply_config(self, cfg: AppConfig) -> None:
        """热更新窗口/超时/归档等参数；network/相机/DIO 映射需重启，不在此处理。"""
        self.cfg = cfg
        self.tracks.update_config(cfg)
        self.scheduler.update_config(cfg)
        self.decision.update_config(cfg)
        self.session.update_config(idle_off_delay_ms=cfg.trigger.idle_off_delay_ms,
                                  trigger_timeout_ms=cfg.trigger.trigger_timeout_ms,
                                  manage_camera=cfg.trigger.scan_mode == "continuous_window")
        self.archive.update_config(cfg.storage)
        self.report.update_config(cfg.report)
        logger.info("运行参数已热更新")

    # ================================================================== 生命周期
    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)

        def _done(task: asyncio.Task) -> None:
            self._bg.discard(task)
            if not task.cancelled() and task.exception() is not None:
                logger.error("后台任务异常: %r", task.exception())
        t.add_done_callback(_done)
        return t

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._set_state(SystemState.LOAD_CONFIG)
        self.bus.bind_loop()
        try:
            await self.repo.open()
            await self.repo.close_stale_alarms(self.clock.now())
            await self.repo.save_config_snapshot(config_to_dict(self.cfg), self.config_version,
                                                 self.clock.now())
        except DbError as e:
            self.alarms.raise_alarm(AlarmCode.DB_ERROR, f"数据库不可用: {e}", "ERROR", key="db")
        self._set_state(SystemState.INIT_DEVICES)
        self._tasks = [asyncio.create_task(self._event_loop(), name="runtime_events"),
                       asyncio.create_task(self._tick_loop(), name="runtime_tick"),
                       asyncio.create_task(self._persist_loop(), name="runtime_persist")]
        await self.report.start()
        await self.dio.start()
        await self.camera.start()
        self._set_state(SystemState.SELF_CHECK)
        await self._self_check()

    async def _self_check(self) -> None:
        t = self.cfg.runtime.self_check_timeout_s
        dio_ok, offline = await asyncio.gather(self.dio.wait_online(t), self.camera.wait_ready(t))
        self._offline |= set(offline)
        if not dio_ok:
            self._offline.add(self.dio.DEVICE_ID)
        if self._offline:
            logger.error("自检未通过，离线设备: %s", sorted(self._offline))
        else:
            logger.info("自检通过")
        self._set_state(SystemState.READY)
        self._refresh_state()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._set_state(SystemState.STOPPING)
        for t in self._tasks[:2]:                       # 先停事件消费与 tick
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for t in list(self._bg):
            t.cancel()
        if self.plc is not None:
            with contextlib.suppress(Exception):
                await self.plc.notify_ready(False)
        await self.session.force_stop()
        await self.flush(5.0)                            # 落盘待写数据（README §11.19）
        self._tasks[2].cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._tasks[2]
        self._tasks = []
        await self.dio.stop()
        await self.camera.stop()
        await self.report.stop()
        await self.repo.close()
        self.tracks.reset()
        logger.info("运行时已停止")

    async def flush(self, timeout_s: float = 5.0) -> bool:
        """等待持久化队列写完；超时返回 False。"""
        try:
            await asyncio.wait_for(self._persist_q.join(), timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    # ================================================================== 状态机
    def _set_state(self, new: SystemState) -> None:
        if new is self.state:
            return
        old, self.state = self.state, new
        logger.info("系统状态 %s -> %s", old.value, new.value)
        if self.plc is not None and new in (SystemState.RUNNING, SystemState.FAULT):
            self._spawn(self._plc_ready(new is SystemState.RUNNING))
        self._notify("state", {"old": old.value, "new": new.value})

    async def _plc_ready(self, value: bool) -> None:
        with contextlib.suppress(Exception):
            assert self.plc is not None
            await self.plc.notify_ready(value)

    def _refresh_state(self) -> None:
        if self.state in _NOT_RUNNABLE:
            return
        cams = set(self.camera.camera_ids)
        fault = (self.dio.DEVICE_ID in self._offline) or (bool(cams) and cams <= self._offline)
        self._set_state(SystemState.FAULT if fault else SystemState.RUNNING)

    def _offline_cameras(self) -> set[str]:
        return self._offline & set(self.camera.camera_ids)

    # ================================================================== 循环
    async def _event_loop(self) -> None:
        while True:
            ev = await self.bus.get()
            try:
                await self.handle_event(ev)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("事件处理异常: %s", ev.event_type.value)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.runtime.tick_interval_ms / 1000.0)
            if not self._tick_pending:             # 合并：上一次 tick 未消费前不再投递
                self._tick_pending = True
                self.bus.publish(make_event(EventType.TIMER_TRIGGER, "runtime", ts=self.clock.now()))

    async def handle_event(self, ev: AppEvent) -> None:
        t, p = ev.event_type, ev.payload
        if t in (EventType.PE_RISE, EventType.PE_FALL):
            sensor, rise = p.get("sensor"), t is EventType.PE_RISE
            if sensor == "PE1":
                self._pe1_rise(ev.ts) if rise else self._pe1_fall(ev.ts)
            elif sensor == "PE2":
                self._pe2_rise(ev.ts) if rise else self._pe2_fall(ev.ts)
        elif t is EventType.CAMERA_RESULT:
            await self._on_camera_result(p["result"])
        elif t is EventType.TIMER_TRIGGER:
            self._tick_pending = False
            await self.tick()
        elif t is EventType.DEVICE_FAULT:
            self._on_device_fault(ev)
        elif t is EventType.DEVICE_RECOVER:
            self._on_device_recover(ev)
        elif t is EventType.OPERATOR_CMD:
            self._on_command(ev)
        elif t is EventType.TRACK_TIMEOUT:
            await self.tick()

    # ================================================================== 光电事件
    def _pe1_rise(self, ts: float) -> None:
        track = self.tracks.create_track(ts, self.mode)
        if self._offline_cameras():
            track.add_alarm(AlarmCode.DEVICE_OFFLINE.value)
        for code in _RAISED_AT_PE:
            if code in track.alarm_codes:
                self.alarms.raise_alarm(code, f"{track.track_id}: 箱距过小或光电时序异常", "ERROR")

    def _pe1_fall(self, ts: float) -> None:
        track = self.tracks.match_last_open_track()
        if track is None:
            self.alarms.raise_alarm(AlarmCode.SENSOR_SEQUENCE_ERROR, "PE1 下降沿没有对应轨迹", "ERROR")
            return
        self.scheduler.prepare_window_close(track, ts)

    def _pe2_rise(self, ts: float) -> None:
        track = self.tracks.match_track_for_pe2(ts)
        if track is None:
            self.alarms.raise_alarm(AlarmCode.SENSOR_SEQUENCE_ERROR, "PE2 上升沿没有对应轨迹", "ERROR")
            return
        self.tracks.attach_pe2(track, ts)
        self.scheduler.open_scan_window(track, self.mode)
        self.last_speed = track.speed_mm_s
        self._spawn(self._ensure_session())

    def _pe2_fall(self, ts: float) -> None:
        track = self.tracks.match_track_for_pe2_fall()
        if track is not None:
            track.pe2_off_ts = ts

    async def _ensure_session(self) -> None:
        if await self.session.ensure_running():
            self.alarms.clear_alarm(AlarmCode.CAM_TIMEOUT, "session")
        else:
            self.alarms.raise_alarm(AlarmCode.CAM_TIMEOUT, f"扫码会话启动失败: {self.session.last_error}",
                                    "ERROR", key="session")

    # ================================================================== 相机结果
    async def _on_camera_result(self, result: CameraResult) -> None:
        out = self.binder.bind_detailed(result, self.tracks.get_active_tracks())
        track = out.track
        if track is None:
            if out.reason == "ambiguous":
                for t in out.candidates:
                    t.gap_violation = True
                    t.add_alarm(AlarmCode.TRACK_OVERLAP.value)
                    t.add_alarm(AlarmCode.AMBIGUOUS.value)
                self.alarms.raise_alarm(AlarmCode.TRACK_OVERLAP, "相邻箱读码窗口重叠，结果归属不唯一", "ERROR")
            elif result.success:
                self.alarms.raise_alarm(AlarmCode.UNBOUND_RESULT, f"结果无对应窗口: {result.code}", "WARN",
                                        key=result.code or "",
                                        suppress_ms=self.cfg.trigger.unbound_alarm_suppress_ms)
            if result.success:
                self._queue_persist(("camera_result", result))
            return
        track.camera_results.append(result)
        if result.success:
            self.scheduler.note_first_ok(track, result.result_ts)

    # ================================================================== 定时处理
    async def tick(self, now: Optional[float] = None) -> None:
        now = self.clock.now() if now is None else now
        if self.cfg.trigger.scan_mode == "burst_trigger":
            for track, plan in self.scheduler.pop_due_triggers(now):
                self._spawn(self._send_trigger(track, plan.camera_id))
        self.scheduler.close_expired_windows(now)
        for t in self.scheduler.collect_due(now):
            await self._finalize(t, now)
        for t in self.tracks.cleanup_expired(now):
            await self._finalize_expired(t, now)
        self._auto_clear_alarms(now)
        await self.session.stop_if_idle(now)

    async def _send_trigger(self, track: BoxTrack, camera_id: str) -> None:
        try:
            await self.camera.trigger_once(camera_id, track.track_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("触发相机失败: %s", e, **track_extra(track.track_id, camera_id))

    def _auto_clear_alarms(self, now: float) -> None:
        for a in self.alarms.list_active_alarms():
            if a.code in _AUTO_CLEAR and now - a.last_ts >= _AUTO_CLEAR_AFTER_S:
                self.alarms.clear_alarm(a.code, a.key)

    # ================================================================== 判定与输出
    async def _finalize(self, track: BoxTrack, now: float) -> None:
        status = self.decision.evaluate(track)
        track.finalized_ts = now
        self.tracks.finalize_track(track.track_id, status, now)
        self.scheduler.forget(track)
        await self._complete(track, status)

    async def _finalize_expired(self, track: BoxTrack, now: float) -> None:
        status = self.decision.evaluate(track)       # cleanup_expired 已回收，这里重新判定
        track.finalized_ts = now
        self.scheduler.forget(track)
        await self._complete(track, status)

    async def _complete(self, track: BoxTrack, status: DecisionStatus) -> None:
        self.counters[status.value] += 1
        self.counters["total"] += 1
        if track.speed_mm_s:
            self.last_speed = track.speed_mm_s
        if status.is_ok:
            for code in _CLEAR_ON_OK:
                self.alarms.clear_alarm(code, "")
        for code in track.alarm_codes:
            if code in (AlarmCode.DEVICE_OFFLINE.value,) or code in _RAISED_AT_PE:
                continue
            self.alarms.raise_alarm(code, f"{track.track_id}: {code}", _ALARM_LEVEL.get(code, "WARN"))
        await self._output(track, status)
        if self.decision.register_outcome(status):
            self.alarms.raise_alarm(AlarmCode.CONSECUTIVE_NO_READ,
                                    f"连续 {self.cfg.trigger.consecutive_no_read_alarm} 箱未读到码", "ERROR")
        row = self._row(track)
        self.recent.append(row)
        self._queue_persist(("track", track))
        self._notify("track_finalized", row)
        logger.info("decision=%s code=%s close=%s alarms=%s", status.value, track.final_code,
                    track.scan_close_reason, ",".join(track.alarm_codes) or "-",
                    **track_extra(track.track_id))

    async def _output(self, track: BoxTrack, status: DecisionStatus) -> None:
        signals = DecisionEngine.output_signals(status)
        for name in signals:
            try:
                await self.dio.write_pulse(name)
                if name == "reject":
                    self.alarms.clear_alarm(AlarmCode.REJECT_FAIL, "")
            except Exception as e:  # noqa: BLE001
                logger.error("输出 %s 失败: %s", name, e, **track_extra(track.track_id))
                if name == "reject":
                    self.alarms.raise_alarm(AlarmCode.REJECT_FAIL, f"剔除输出失败: {e}", "ERROR")
                else:
                    self.alarms.raise_alarm(AlarmCode.DEVICE_OFFLINE, f"DIO 输出失败: {e}", "ERROR",
                                            key=self.dio.DEVICE_ID)
        if self.plc is not None:
            try:
                await self.plc.notify_scan_result(status, track.track_id)
                if "reject" in signals:
                    await self.plc.notify_reject(track.track_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("PLC 通知失败: %s", e)

    @staticmethod
    def _row(track: BoxTrack) -> dict:
        proc = None
        if track.finalized_ts is not None:
            proc = round((track.finalized_ts - track.created_ts) * 1000.0, 1)
        return {"track_id": track.track_id, "time": iso_ts(track.created_ts),
                "status": track.final_status.value if track.final_status else "",
                "code": track.final_code or "", "speed_mm_s": track.speed_mm_s,
                "length_mm": track.length_mm, "alarms": list(track.alarm_codes), "process_ms": proc}

    # ================================================================== 设备事件
    def _on_device_fault(self, ev: AppEvent) -> None:
        p = ev.payload
        did = p.get("device_id", "")
        code = p.get("alarm_code", AlarmCode.DEVICE_OFFLINE.value)
        msg = f"{did}: {p.get('message', '')}"
        if code == AlarmCode.PE_STUCK.value:
            self.alarms.raise_alarm(AlarmCode.PE_STUCK, msg, "ERROR", key=did)
        else:
            self._offline.add(did)
            self.alarms.raise_alarm(AlarmCode.DEVICE_OFFLINE, msg, "ERROR", key=did)
            if p.get("device_type") == "camera":
                for t in self.tracks.get_active_tracks():
                    t.add_alarm(AlarmCode.DEVICE_OFFLINE.value)
        self._queue_persist(("event", ev))
        self._refresh_state()

    def _on_device_recover(self, ev: AppEvent) -> None:
        p = ev.payload
        did = p.get("device_id", "")
        code = p.get("alarm_code", AlarmCode.DEVICE_OFFLINE.value)
        if code == AlarmCode.PE_STUCK.value:
            self.alarms.clear_alarm(AlarmCode.PE_STUCK, did)
        else:
            self._offline.discard(did)
            self.alarms.clear_alarm(AlarmCode.DEVICE_OFFLINE, did)
        self._queue_persist(("event", ev))
        self._refresh_state()

    # ================================================================== 操作指令
    def _on_command(self, ev: AppEvent) -> None:
        p = ev.payload
        cmd = p.get("cmd")
        self._queue_persist(("event", ev))
        if cmd == "clear_alarms":
            keep = {AlarmCode.DEVICE_OFFLINE.value, AlarmCode.PE_STUCK.value}
            for a in self.alarms.list_active_alarms():
                if a.code not in keep:
                    self.alarms.clear_alarm(a.code, a.key)
        elif cmd == "ack":
            self.alarms.ack(p.get("code", ""), p.get("user", "operator"), p.get("key", ""))
        elif cmd == "reset_counters":
            self.counters.clear()
            self.recent.clear()
        elif cmd == "set_mode":
            try:
                mode = RunMode(p.get("mode"))
            except ValueError:
                logger.warning("非法运行模式: %r", p.get("mode"))
                return
            if self.tracks.active_count > 0:
                logger.warning("有活动轨迹，拒绝切换模式（仅待机时允许）")
                return
            self.mode = mode
            logger.info("运行模式切换为 %s", mode.value)
            self._notify("mode", mode.value)
        else:
            logger.warning("未知操作指令: %r", cmd)

    # ================================================================== 持久化
    def _on_alarm(self, event: str, alarm: Any) -> None:
        d = alarm.to_dict()
        if event in ("raised", "cleared", "acked"):
            self._queue_persist(("alarm", d))
        self._notify("alarm", {"event": event, "alarm": d})

    def _queue_persist(self, item: tuple[str, Any]) -> None:
        try:
            self._persist_q.put_nowait(item)
        except asyncio.QueueFull:
            logger.error("持久化队列已满，丢弃 %s", item[0])

    async def _persist_loop(self) -> None:
        while True:
            item = await self._persist_q.get()
            try:
                await self._persist_item(item)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("持久化异常: %s", item[0])
            finally:
                self._persist_q.task_done()

    async def _persist_item(self, item: tuple[str, Any]) -> None:
        kind, obj = item
        if kind == "track":
            await self._persist_track(obj)
        elif kind == "event":
            await self._db(lambda: self.repo.save_event(obj))
        elif kind == "alarm":
            await self._db(lambda: self.repo.save_alarm(obj))
        elif kind == "camera_result":
            await self._db(lambda: self.repo.save_camera_result(obj, None))

    async def _db(self, factory: Callable[[], Awaitable[None]]) -> bool:
        err: Optional[Exception] = None
        for attempt in range(3):
            try:
                await factory()
                self.alarms.clear_alarm(AlarmCode.DB_ERROR, "db")
                return True
            except DbError as e:
                err = e
                await asyncio.sleep(0.2 * (attempt + 1))
        self.alarms.raise_alarm(AlarmCode.DB_ERROR, f"数据库写入失败: {err}", "ERROR", key="db")
        return False

    async def _persist_track(self, track: BoxTrack) -> None:
        dev, line = self.cfg.app.device_id, self.cfg.app.line_id
        await self._db(lambda: self.repo.save_scan_record(track, device_id=dev, line_id=line))
        await self._db(lambda: self.repo.save_camera_results(track.camera_results, track.track_id))
        await self.archive.archive_track(track, dev, line)
        payload = track_summary(track, dev, line)
        payload["created_at"] = iso_ts(track.created_ts)
        await self.report.submit(payload)
