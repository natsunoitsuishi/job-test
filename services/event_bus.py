"""轻量内存事件总线（README §11.8）。

设计要点：
- 单个 asyncio.Queue，设备侧 publish() 同步入队，严格保持事件先后顺序。
  （旧实现 emit() 里用 create_task(publish()) 入队，PE1/PE2 等事件可能乱序。）
- 只有 RuntimeService 一个消费者，串行处理所有业务事件，杜绝竞态。
- 其他线程（如 UI）使用 publish_threadsafe()。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from domain.events import AppEvent

logger = logging.getLogger("vg.runtime.bus")


class EventBus:
    def __init__(self, max_size: int = 10_000) -> None:
        self._queue: asyncio.Queue[AppEvent] = asyncio.Queue(maxsize=max_size)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.stats = {"published": 0, "dropped": 0, "peak": 0}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: AppEvent) -> bool:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.stats["dropped"] += 1
            logger.error("事件队列已满，丢弃 %r", event)
            return False
        self.stats["published"] += 1
        size = self._queue.qsize()
        if size > self.stats["peak"]:
            self.stats["peak"] = size
        return True

    def publish_threadsafe(self, event: AppEvent) -> None:
        loop = self._loop or asyncio.get_event_loop()
        loop.call_soon_threadsafe(self.publish, event)

    async def get(self) -> AppEvent:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()