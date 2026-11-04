"""上报服务（README §11.17 / §11.18）：调度上位机 + MES。

- 直发成功即结束；失败写入 SQLite report_queue（重启保留），后台按 retry_interval_s 顺序补发；
- 某目标存在积压时，新数据直接入队，保证到达顺序；
- 失败时报 REPORT_FAIL(key=目标)，积压清空后恢复；
- 同一把锁保护「直发 / 补发」，避免乱序。
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Optional

from config.schema import ReportSection
from devices.report.base import MesClient, SchedulerClient
from domain.alarm_service import AlarmService
from domain.enums import AlarmCode
from infra.db.repository import DbError, ScanRepository
from infra.logging import get_logger
from infra.util.clock import SYSTEM_CLOCK, Clock

logger = get_logger("report")


class ReportService:
    def __init__(self, cfg: ReportSection, scheduler: SchedulerClient, mes: MesClient,
                 repo: ScanRepository, alarms: AlarmService, clock: Clock = SYSTEM_CLOCK) -> None:
        self.cfg = cfg
        self.scheduler = scheduler
        self.mes = mes
        self.repo = repo
        self.alarms = alarms
        self._clock = clock
        self._lock = asyncio.Lock()
        self._pending: dict[str, int] = {"scheduler": 0, "mes": 0}
        self._task: Optional[asyncio.Task] = None
        self.sent = 0
        self.failed = 0

    def update_config(self, cfg: ReportSection) -> None:
        self.cfg = cfg

    def _enabled(self) -> list[str]:
        return [t for t, c in (("scheduler", self.scheduler), ("mes", self.mes)) if c.enabled]

    # ------------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        for c in (self.scheduler, self.mes):
            if c.enabled:
                try:
                    await c.connect()
                except Exception as e:  # noqa: BLE001 启动时连不上不阻断，后续发送时重连
                    logger.warning("上报客户端首次连接失败: %s", e)
        for t in self._enabled():
            try:
                self._pending[t] = await self.repo.report_queue_size(t)
            except DbError:
                self._pending[t] = 0
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._retry_loop(), name="report_retry")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for c in (self.scheduler, self.mes):
            with contextlib.suppress(Exception):
                await c.disconnect()

    # ------------------------------------------------------------------ 发送
    async def _send(self, target: str, payload: dict) -> bool:
        try:
            if target == "scheduler":
                ok = bool(await self.scheduler.report_result(payload))
            else:
                ok = bool(await self.mes.report_scan_record(payload))
        except Exception as e:  # noqa: BLE001
            logger.warning("上报 %s 异常: %s", target, e)
            ok = False
        if ok:
            self.sent += 1
        else:
            self.failed += 1
        return ok

    async def _enqueue(self, target: str, payload: dict) -> None:
        try:
            await self.repo.enqueue_report(target, payload, self.cfg.max_cache, self._clock.now())
            self._pending[target] = await self.repo.report_queue_size(target)
        except DbError as e:
            logger.error("上报缓存写入失败，数据丢弃: %s", e)

    async def submit(self, payload: dict) -> None:
        """提交一箱结果。不抛异常。"""
        async with self._lock:
            for target in self._enabled():
                if self._pending.get(target, 0) == 0 and await self._send(target, payload):
                    self.alarms.clear_alarm(AlarmCode.REPORT_FAIL, target)
                    continue
                await self._enqueue(target, payload)
                self.alarms.raise_alarm(AlarmCode.REPORT_FAIL, f"{target} 上报失败，已缓存待重发", "WARN", key=target)

    async def flush(self, target: str, limit: int = 20) -> int:
        """按入队顺序补发，遇到第一次失败即停止。返回成功条数。"""
        async with self._lock:
            sent = 0
            try:
                items = await self.repo.peek_reports(target, limit)
                for it in items:
                    if await self._send(target, it["payload"]):
                        await self.repo.ack_reports([it["id"]])
                        sent += 1
                    else:
                        await self.repo.fail_reports([it["id"]])
                        break
                self._pending[target] = await self.repo.report_queue_size(target)
            except DbError as e:
                logger.error("上报缓存读写失败: %s", e)
                return sent
            if self._pending[target] == 0:
                self.alarms.clear_alarm(AlarmCode.REPORT_FAIL, target)
            return sent

    async def _retry_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.retry_interval_s)
            for target in self._enabled():
                if self._pending.get(target, 0) <= 0:
                    continue
                try:
                    n = await self.flush(target)
                    if n:
                        logger.info("补发 %s 成功 %d 条，剩余 %d", target, n, self._pending[target])
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("补发异常")

    async def send_heartbeat(self, payload: dict) -> bool:
        if not self.scheduler.enabled:
            return True
        try:
            return bool(await self.scheduler.report_heartbeat(payload))
        except Exception as e:  # noqa: BLE001
            logger.warning("调度心跳异常: %s", e)
            return False

    def snapshot(self) -> dict:
        return {"pending": dict(self._pending), "sent": self.sent, "failed": self.failed,
                "enabled": self._enabled()}
