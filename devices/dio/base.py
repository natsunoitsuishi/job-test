"""DI/DO 抽象基类（README §11.9 DiDoService）。

职责：周期轮询 → 去抖 → 边沿事件(pe_event) → PE 持续遮挡检测 → 脉冲输出 → 断线重连。
子类只实现 _open/_close/_read_inputs/_write_output。
首次/重连成功后的第一次轮询只建立基线，不产生边沿事件（避免重连后出现幻影来料）。
"""
from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from collections import deque
from typing import Optional

from config.schema import DioSection
from devices.common import DeviceError, EventSink, fault_event, recover_event
from domain.enums import AlarmCode, DeviceStatus
from domain.events import pe_event
from domain.models import DeviceHealth
from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

logger = get_logger("dio")

_PE_NAMES = ("pe1", "pe2")


class DioError(DeviceError):
    pass


class BaseDiDoService(ABC):
    DEVICE_ID = "DIO"

    def __init__(self, cfg: DioSection, emit: EventSink, clock: Clock = SYSTEM_CLOCK) -> None:
        self.cfg = cfg
        self._emit = emit
        self._clock = clock
        self._di_names: dict[int, str] = {ch: name for name, ch in cfg.di_map.items()}
        self._do_names: dict[int, str] = {ch: name for name, ch in cfg.do_map.items()}
        self._stable: dict[int, bool] = {ch: False for ch in cfg.di_map.values()}
        self._cand: dict[int, tuple[bool, float]] = {}
        self._on_since: dict[int, float] = {}
        self._stuck: set[int] = set()
        self.do_state: dict[int, bool] = {ch: False for ch in cfg.do_map.values()}
        self.pulse_log: deque[tuple[float, str, int]] = deque(maxlen=200)
        self._pulses: dict[int, asyncio.Task] = {}
        self._task: Optional[asyncio.Task] = None
        self._online = False
        self._fault_reported = False
        self._need_baseline = False
        self._last_poll_ts: Optional[float] = None
        self._message = ""

    # ------------------------------------------------------------------ 子类实现
    @abstractmethod
    async def _open(self) -> None: ...

    @abstractmethod
    async def _close(self) -> None: ...

    @abstractmethod
    async def _read_inputs(self) -> dict[int, bool]:
        """返回 {通道号: 电平}，至少覆盖 di_map 的所有通道。"""

    @abstractmethod
    async def _write_output(self, channel: int, value: bool) -> None: ...

    # ------------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self._try_open()
        self._task = asyncio.create_task(self._watch_loop(), name="dio_watch")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for t in list(self._pulses.values()):
            t.cancel()
        self._pulses.clear()
        if self._online:
            for ch, on in list(self.do_state.items()):
                if on:
                    with contextlib.suppress(Exception):
                        await self._write_output(ch, False)
                    self.do_state[ch] = False
        await self._safe_close()
        self._online = False

    async def wait_online(self, timeout_s: float) -> bool:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout_s
        while True:
            if self._online:
                return True
            if loop.time() >= end:
                return False
            await asyncio.sleep(0.05)

    async def _safe_close(self) -> None:
        with contextlib.suppress(Exception):
            await self._close()

    async def _try_open(self) -> bool:
        try:
            await self._open()
        except Exception as e:  # noqa: BLE001
            await self._safe_close()
            self._mark_offline(f"{type(e).__name__}: {e}")
            return False
        self._need_baseline = True
        self._mark_online()
        return True

    def _mark_online(self) -> None:
        if self._online:
            return
        self._online = True
        self._message = ""
        logger.info("DIO 在线 driver=%s", self.cfg.driver)
        if self._fault_reported:
            self._fault_reported = False
            self._emit(recover_event(self.DEVICE_ID, "dio", ts=self._clock.now()))

    def _mark_offline(self, message: str) -> None:
        self._online = False
        self._message = message
        if not self._fault_reported:
            self._fault_reported = True
            logger.error("DIO 离线: %s", message)
            self._emit(fault_event(self.DEVICE_ID, "dio", message, ts=self._clock.now()))

    async def _watch_loop(self) -> None:
        poll_s = self.cfg.poll_interval_ms / 1000.0
        while True:
            try:
                if not self._online:
                    await asyncio.sleep(self.cfg.reconnect_interval_s)
                    await self._try_open()
                    continue
                await self.poll_once()
            except Exception as e:  # noqa: BLE001
                await self._safe_close()
                self._mark_offline(f"轮询失败: {type(e).__name__}: {e}")
                continue
            await asyncio.sleep(poll_s)

    # ------------------------------------------------------------------ 输入处理
    async def poll_once(self, now: Optional[float] = None) -> None:
        """读一次输入并处理边沿。测试/仿真可直接调用（配合 ManualClock）。"""
        raw = await self._read_inputs()
        t = self._clock.now() if now is None else now
        self._last_poll_ts = t
        self._process(raw, t)

    def _process(self, raw: dict[int, bool], now: float) -> None:
        baseline, self._need_baseline = self._need_baseline, False
        debounce_s = self.cfg.debounce_ms / 1000.0
        for ch in self._stable:
            if ch not in raw:
                continue
            val = bool(raw[ch])
            if baseline:
                self._stable[ch] = val
                self._cand.pop(ch, None)
                if val:
                    self._on_since[ch] = now
                else:
                    self._on_since.pop(ch, None)
                continue
            if val == self._stable[ch]:
                self._cand.pop(ch, None)
            else:
                cand = self._cand.get(ch)
                if cand is None or cand[0] != val:
                    cand = (val, now)
                    self._cand[ch] = cand
                if now - cand[1] >= debounce_s:
                    self._cand.pop(ch, None)
                    self._accept(ch, val, cand[1])   # 边沿时间取首次变化时刻
            self._check_stuck(ch, now)

    def _accept(self, ch: int, val: bool, ts: float) -> None:
        self._stable[ch] = val
        name = self._di_names[ch]
        if val:
            self._on_since[ch] = ts
        else:
            self._on_since.pop(ch, None)
            if ch in self._stuck:
                self._stuck.discard(ch)
                self._emit(recover_event(f"dio:{name}", "photoelectric",
                                         AlarmCode.PE_STUCK.value, ts=ts))
        if name in _PE_NAMES:
            self._emit(pe_event(name.upper(), val, ts))

    def _check_stuck(self, ch: int, now: float) -> None:
        name = self._di_names.get(ch, "")
        if name not in _PE_NAMES or not self._stable[ch] or ch in self._stuck:
            return
        since = self._on_since.get(ch)
        if since is not None and now - since >= self.cfg.pe_stuck_on_ms / 1000.0:
            self._stuck.add(ch)
            msg = f"{name.upper()} 持续遮挡超过 {self.cfg.pe_stuck_on_ms} ms"
            logger.error(msg)
            self._emit(fault_event(f"dio:{name}", "photoelectric", msg,
                                   AlarmCode.PE_STUCK.value, ts=now))

    async def read_di(self, channel: int) -> bool:
        return bool(self._stable.get(channel, False))

    @property
    def pe_state(self) -> dict[str, bool]:
        return {name: self._stable[ch] for ch, name in self._di_names.items()}

    # ------------------------------------------------------------------ 输出
    async def write_do(self, channel: int, value: bool) -> None:
        if not self._online:
            raise DioError("DIO 离线，无法输出")
        try:
            await self._write_output(channel, value)
        except DioError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            await self._safe_close()
            self._mark_offline(f"输出失败: {msg}")
            raise DioError(msg) from e
        self.do_state[channel] = value

    async def write_pulse(self, name: str, ms: Optional[int] = None) -> None:
        """输出脉冲：立刻置位，后台定时复位。同通道重复触发会延长脉冲。"""
        ch = self.cfg.do_map.get(name)
        if ch is None:
            raise DioError(f"未映射的输出: {name}")
        width = int(ms if ms is not None else self.cfg.pulse_ms.get(name, 100))
        old = self._pulses.pop(ch, None)
        if old is not None:
            old.cancel()
        await self.write_do(ch, True)
        self.pulse_log.append((self._clock.now(), name, width))
        self._pulses[ch] = asyncio.create_task(self._pulse_off(ch, width / 1000.0))

    async def _pulse_off(self, ch: int, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
            await self.write_do(ch, False)
        except DioError as e:
            logger.error("脉冲复位失败 ch=%s: %s", ch, e)
        finally:
            if self._pulses.get(ch) is asyncio.current_task():
                self._pulses.pop(ch, None)

    # ------------------------------------------------------------------ 状态
    @property
    def online(self) -> bool:
        return self._online

    def get_health(self) -> DeviceHealth:
        return DeviceHealth(self.DEVICE_ID, "dio",
                            DeviceStatus.ONLINE if self._online else DeviceStatus.OFFLINE,
                            self._last_poll_ts, self._message)

    def snapshot(self) -> dict:
        return {"online": self._online, "driver": self.cfg.driver, "message": self._message,
                "di": self.pe_state,
                "do": {self._do_names[ch]: v for ch, v in self.do_state.items()},
                "stuck": [self._di_names[c] for c in self._stuck],
                "pulses": list(self.pulse_log)[-5:]}
