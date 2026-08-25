"""Queue / Lag 监控 — 检测队列积压、receive lag、event lag。

依据：epic-02 Task 02-E, docs/DATA_HEALTH.md §6
监控各 stage 的队列深度和延迟，防止：
- 队列积压导致 Feature Engine 跟不上
- receive lag（接收延迟）过大
- event lag（事件时间 vs 接收时间差）过大
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.clock import Clock, SystemClock

logger = logging.getLogger(__name__)


@dataclass
class LagMetrics:
    """单个 stream/stage 的延迟指标。"""

    receive_lag_ms: int = 0  # now - last_receive_time
    event_lag_ms: int = 0    # last_receive_time - last_event_time
    max_receive_lag_ms: int = 0
    max_event_lag_ms: int = 0
    sample_count: int = 0


@dataclass
class QueueMetrics:
    """单个队列的指标。"""

    depth: int = 0
    max_depth: int = 0
    total_enqueued: int = 0
    total_dequeued: int = 0

    @property
    def backlog(self) -> int:
        """积压量。"""
        return self.depth


class QueueLagMonitor:
    """队列与延迟监控器。

    追踪各 stage 的队列深度和流延迟。
    当队列积压或延迟超阈值时发出警告。
    """

    def __init__(
        self,
        clock: Clock | None = None,
        queue_depth_warn: int = 1000,
        receive_lag_warn_ms: int = 5000,
        event_lag_warn_ms: int = 10_000,
    ) -> None:
        self.clock = clock or SystemClock()
        self.queue_depth_warn = queue_depth_warn
        self.receive_lag_warn_ms = receive_lag_warn_ms
        self.event_lag_warn_ms = event_lag_warn_ms

        self._queue_metrics: dict[str, QueueMetrics] = {}
        self._lag_metrics: dict[str, LagMetrics] = {}

    # ── 队列监控 ──

    def register_queue(self, stage: str) -> QueueMetrics:
        """注册一个队列。"""
        if stage not in self._queue_metrics:
            self._queue_metrics[stage] = QueueMetrics()
        return self._queue_metrics[stage]

    def record_enqueue(self, stage: str, n: int = 1) -> None:
        """记录入队。"""
        m = self._queue_metrics.get(stage)
        if m:
            m.depth += n
            m.total_enqueued += n
            m.max_depth = max(m.max_depth, m.depth)
            if m.depth > self.queue_depth_warn:
                logger.warning(
                    "queue_backlog stage=%s depth=%d warn_threshold=%d",
                    stage,
                    m.depth,
                    self.queue_depth_warn,
                )

    def record_dequeue(self, stage: str, n: int = 1) -> None:
        """记录出队。"""
        m = self._queue_metrics.get(stage)
        if m:
            m.depth = max(0, m.depth - n)
            m.total_dequeued += n

    def get_queue_metrics(self, stage: str) -> QueueMetrics | None:
        return self._queue_metrics.get(stage)

    # ── 延迟监控 ──

    def register_lag(self, stream: str) -> LagMetrics:
        """注册一个流的延迟监控。"""
        if stream not in self._lag_metrics:
            self._lag_metrics[stream] = LagMetrics()
        return self._lag_metrics[stream]

    def record_lag(
        self,
        stream: str,
        event_time: int,
        receive_time: int | None = None,
    ) -> LagMetrics:
        """记录一次事件的时间延迟。

        Args:
            stream: 流标识
            event_time: 事件时间（交易所时间）
            receive_time: 本地接收时间，默认用 clock.now_ms()
        """
        recv = receive_time if receive_time is not None else self.clock.now_ms()
        now = self.clock.now_ms()

        receive_lag = now - recv
        event_lag = recv - event_time

        m = self._lag_metrics.get(stream)
        if m is None:
            m = LagMetrics()
            self._lag_metrics[stream] = m

        m.receive_lag_ms = receive_lag
        m.event_lag_ms = event_lag
        m.max_receive_lag_ms = max(m.max_receive_lag_ms, receive_lag)
        m.max_event_lag_ms = max(m.max_event_lag_ms, event_lag)
        m.sample_count += 1

        if receive_lag > self.receive_lag_warn_ms:
            logger.warning(
                "receive_lag_high stream=%s lag=%dms warn=%dms",
                stream,
                receive_lag,
                self.receive_lag_warn_ms,
            )
        if event_lag > self.event_lag_warn_ms:
            logger.warning(
                "event_lag_high stream=%s lag=%dms warn=%dms",
                stream,
                event_lag,
                self.event_lag_warn_ms,
            )

        return m

    def get_lag_metrics(self, stream: str) -> LagMetrics | None:
        return self._lag_metrics.get(stream)

    def get_all_queue_metrics(self) -> dict[str, QueueMetrics]:
        return dict(self._queue_metrics)

    def get_all_lag_metrics(self) -> dict[str, LagMetrics]:
        return dict(self._lag_metrics)
