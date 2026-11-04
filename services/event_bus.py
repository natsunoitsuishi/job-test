"""事件总线（README §11.8）：asyncio.Queue 单队列，设备/用户操作的事件统一入队，RuntimeService 串行消费。

- publish 线程安全：在 bind_loop 绑定的事件循环之外调用时自动转交 call_soon_threadsafe；
- 队列满时优先丢弃 CAMERA_HEARTBEAT，其余事件丢弃最旧一条并计数；
- subscribe 的回调在发布线程同步执行，只给 UI/调试做只读旁听，回调异常被吞掉。
要求 Python 3.10+（Queue 不绑定创建时的事件循环）。
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from domain.enums import EventType
from domain.models import AppEvent
from infra.logging import get_logger

logger = get_logger("runtime")

_DROPPABLE = {EventType.CAMERA_HEARTBEAT}


class EventBus:
    def __init__(self, maxsize: int = 10000) -> None:
        self._q: asyncio.Queue[AppEvent] = asyncio.Queue(maxsize)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subs: list[Callable[[AppEvent], None]] = []
        self.published = 0
        self.dropped = 0

    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def subscribe(self, cb: Callable[[AppEvent], None]) -> None:
        self._subs.append(cb)

    def publish(self, event: AppEvent) -> None:
        self.published += 1
        for cb in list(self._subs):
            try:
                cb(event)
            except Exception:  # noqa: BLE001
                logger.exception("事件订阅回调异常")
        loop = self._loop
        if loop is not None:
            try:
                running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                loop.call_soon_threadsafe(self._put, event)
                return
        self._put(event)

    emit = publish   # 作为设备层的 EventSink 使用

    def _put(self, event: AppEvent) -> None:
        if self._q.full():
            self.dropped += 1
            if event.event_type in _DROPPABLE:
                return
            try:
                self._q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            if self.dropped % 100 == 1:
                logger.error("事件队列已满，已丢弃 %d 条", self.dropped)
        self._q.put_nowait(event)

    async def get(self) -> AppEvent:
        return await self._q.get()

    def get_nowait(self) -> Optional[AppEvent]:
        try:
            return self._q.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def qsize(self) -> int:
        return self._q.qsize()

    def drain(self) -> list[AppEvent]:
        out: list[AppEvent] = []
        while True:
            ev = self.get_nowait()
            if ev is None:
                return out
            out.append(ev)

