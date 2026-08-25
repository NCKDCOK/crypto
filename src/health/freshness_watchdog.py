"""Freshness Watchdog — 每流独立 freshness budget 判断。

依据：docs/DATA_HEALTH.md §0-§3
核心：connected ≠ healthy。WS socket 可能 open 但长时间无推送（半死状态）。
必须用独立 freshness watchdog 判断 healthy，不能只看 connected。

每个 stream 独立维护 last_receive_time，超过 freshness_budget 即从 OK 降级 STALE。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.clock import Clock, SystemClock
from src.domain import HealthLevel, HealthStatus

logger = logging.getLogger(__name__)


class StreamType(str, Enum):
    """流类型，用于确定 freshness budget。"""

    AGGTRADE = "aggTrade"
    KLINE = "kline"
    OI_POLLER = "oi_poller"
    FUNDING_PREMIUM = "funding_premium"


@dataclass
class FreshnessBudget:
    """每流的 freshness budget 配置。"""

    aggtrade_active_ms: int = 5_000
    aggtrade_low_activity_ms: int = 30_000
    kline_1m_ms: int = 90_000
    oi_poller_ms: int = 10_000  # 2 × 5s poll
    funding_premium_ms: int = 60_000


@dataclass
class StreamState:
    """单个 stream 的运行时状态。"""

    stream: str
    symbol: str | None = None
    stream_type: StreamType | None = None
    connected: bool = False
    subscribed: bool = False
    last_event_time: int | None = None
    last_receive_time: int | None = None
    message_count: int = 0
    reconnect_count: int = 0
    sequence: int | None = None


class FreshnessWatchdog:
    """Freshness watchdog — 监控每个 stream 的数据新鲜度。

    每个 stream 独立维护状态。check_health() 根据当前时间和 budget
    判断是否 STALE。connected ≠ healthy。
    """

    def __init__(
        self,
        budget: FreshnessBudget | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.budget = budget or FreshnessBudget()
        self.clock = clock or SystemClock()
        self._streams: dict[str, StreamState] = {}

    def register_stream(
        self,
        stream: str,
        symbol: str | None = None,
        stream_type: StreamType | None = None,
    ) -> StreamState:
        """注册一个新 stream。"""
        state = StreamState(stream=stream, symbol=symbol, stream_type=stream_type)
        self._streams[stream] = state
        return state

    def get_stream(self, stream: str) -> StreamState | None:
        return self._streams.get(stream)

    def mark_connected(self, stream: str, connected: bool) -> None:
        """更新连接状态。"""
        state = self._streams.get(stream)
        if state:
            state.connected = connected
            if connected:
                state.subscribed = True

    def mark_reconnected(self, stream: str) -> None:
        """记录重连。"""
        state = self._streams.get(stream)
        if state:
            state.reconnect_count += 1

    def record_event(
        self,
        stream: str,
        event_time: int | None = None,
        receive_time: int | None = None,
    ) -> None:
        """记录收到一条事件，更新时间戳。"""
        state = self._streams.get(stream)
        if not state:
            return
        now = self.clock.now_ms()
        state.last_event_time = event_time if event_time is not None else now
        state.last_receive_time = receive_time if receive_time is not None else now
        state.message_count += 1

    def _get_budget_ms(self, state: StreamState) -> int:
        """根据 stream 类型获取 freshness budget。"""
        if state.stream_type == StreamType.AGGTRADE:
            # 活跃 symbol 5s，低活 symbol 30s
            # V1 简化：用 active budget；分级逻辑后续可扩展
            return self.budget.aggtrade_active_ms
        elif state.stream_type == StreamType.KLINE:
            return self.budget.kline_1m_ms
        elif state.stream_type == StreamType.OI_POLLER:
            return self.budget.oi_poller_ms
        elif state.stream_type == StreamType.FUNDING_PREMIUM:
            return self.budget.funding_premium_ms
        else:
            # 未知类型，用保守的 30s
            return 30_000

    def _compute_age_ms(self, state: StreamState) -> int | None:
        """计算 age = now - last_receive_time。"""
        if state.last_receive_time is None:
            return None
        return self.clock.now_ms() - state.last_receive_time

    def check_health(self, stream: str) -> HealthStatus:
        """检查单个 stream 的健康状态。

        判定逻辑：
        1. 未连接 → FAIL
        2. 连接但从未收过数据 → WARN（刚启动）
        3. 连接且 age > budget → STALE（半死状态）
        4. 连接且 age ≤ budget → OK
        """
        state = self._streams.get(stream)
        if state is None:
            return HealthStatus(
                stream=stream,
                status=HealthLevel.FAIL,
                connected=False,
                reason="stream_not_registered",
            )

        age_ms = self._compute_age_ms(state)
        budget_ms = self._get_budget_ms(state)

        if not state.connected:
            status = HealthLevel.FAIL
            reason = "not_connected"
        elif state.last_receive_time is None:
            # 连接了但从未收到数据
            status = HealthLevel.WARN
            reason = "no_data_yet"
        elif age_ms is not None and age_ms > budget_ms:
            status = HealthLevel.STALE
            reason = f"age_{age_ms}ms_exceeds_budget_{budget_ms}ms"
            logger.warning(
                "stream_stale stream=%s age=%dms budget=%dms",
                stream,
                age_ms,
                budget_ms,
            )
        else:
            status = HealthLevel.OK
            reason = None

        stale_seconds = None
        if status in (HealthLevel.STALE, HealthLevel.FAIL) and age_ms is not None:
            stale_seconds = age_ms // 1000

        return HealthStatus(
            stream=stream,
            symbol=state.symbol,
            status=status,
            last_event_time=state.last_event_time,
            last_receive_time=state.last_receive_time,
            age_ms=age_ms,
            stale_seconds=stale_seconds,
            connected=state.connected,
            subscribed=state.subscribed,
            message_count=state.message_count,
            reconnect_count=state.reconnect_count,
            sequence=state.sequence,
            reason=reason,
        )

    def check_all(self) -> list[HealthStatus]:
        """检查所有已注册 stream 的健康状态。"""
        return [self.check_health(s) for s in self._streams]

    def is_healthy(self, stream: str) -> bool:
        """快捷方法：stream 是否健康（OK）。"""
        return self.check_health(stream).status == HealthLevel.OK

    def is_stale(self, stream: str) -> bool:
        """快捷方法：stream 是否 STALE。"""
        return self.check_health(stream).status == HealthLevel.STALE
