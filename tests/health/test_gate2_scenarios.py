"""Gate 2 必测场景集成测试 — 6 个 DATA_HEALTH.md §5 场景。

每个场景一个独立测试，验证完整链路：
1. WS open 但 30s 无 aggTrade → STALE
2. 重连后重复 trade → 丢弃，CVD 前置不双计
3. 5m 前无接近 OI 快照 → oi_change_5m=unavailable（非取 9m 前）
4. 429 + Retry-After → 全局退避，不各模块重试
5. 关键数据 STALE → detector 无权进入 CONFIRMED
6. WS 半死（TCP open 无数据）→ budget 内降级 STALE
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.clock import TestClock
from src.domain import AggressorSide, HealthLevel, OpenInterestSnapshot, TradeEvent
from src.health.confidence import ConfidenceTracker
from src.health.dedup import TradeDedupValidator
from src.health.freshness_watchdog import (
    FreshnessBudget,
    FreshnessWatchdog,
    StreamType,
)
from src.health.oi_lookup import OILookup
from src.health.rate_limiter import (
    CircuitOpenError,
    RateLimiter,
    RateLimiterConfig,
)


# ────────────────────────────────────────────────────────────────────
# 场景 1：WS open 但 30s 无 aggTrade → STALE
# ────────────────────────────────────────────────────────────────────


class TestScenario1StaleNoData:
    """WS socket 仍 open，但 30s 无 aggTrade → 对应 stream → STALE。"""

    def test_ws_open_but_no_data_30s(self):
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(
            budget=FreshnessBudget(aggtrade_active_ms=5000),
            clock=clock,
        )
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.record_event("aggTrade:BTCUSDT", event_time=1000, receive_time=1000)

        # 30s 后，无新数据
        clock.set(31_000)

        hs = wd.check_health("aggTrade:BTCUSDT")
        assert hs.connected is True   # WS 仍 open
        assert hs.status == HealthLevel.STALE  # 但数据不新鲜


# ────────────────────────────────────────────────────────────────────
# 场景 2：重连后重复 trade → 丢弃，CVD 前置不双计
# ────────────────────────────────────────────────────────────────────


class TestScenario2ReconnectDedup:
    """重连后重复一笔 trade → 丢弃重复，CVD 前置数据不双计。"""

    def test_reconnect_duplicate_dropped(self):
        validator = TradeDedupValidator()

        # 正常接收 trade_id 100-105
        accepted = []
        for tid in range(100, 106):
            t = _make_trade("BTCUSDT", tid)
            if validator.validate(t):
                accepted.append(t)

        assert len(accepted) == 6

        # 重连后重发 103-105 → 全部丢弃
        for tid in range(103, 106):
            t = _make_trade("BTCUSDT", tid)
            assert validator.validate(t) is False

        # CVD 前置数据不双计 — 验证 accepted 中没有重复
        trade_ids = [t.trade_id for t in accepted]
        assert len(trade_ids) == len(set(trade_ids))  # 无重复

        # 新的 106 → 接受
        t106 = _make_trade("BTCUSDT", 106)
        assert validator.validate(t106) is True
        assert validator.stats.total_dropped == 3  # 3 个重复被丢弃


# ────────────────────────────────────────────────────────────────────
# 场景 3：5m 前无接近 OI 快照 → unavailable（非取 9m 前）
# ────────────────────────────────────────────────────────────────────


class TestScenario3OILookupUnavailable:
    """5m 前没有足够接近的 OI 快照 → oi_change_5m = unavailable，而非取 9m 前数据。"""

    def test_5m_lookup_no_nearby_snapshot(self):
        lookup = OILookup(default_tolerance_ms=15_000)  # 15s 容差

        # 只有 9 分钟前的快照
        lookup.add_snapshot(_make_oi("BTCUSDT", receive_time=0, oi="100.0"))

        # 当前时间 540000ms（9 分钟后），需要 5 分钟前的快照
        # target = 540000 - 300000 = 240000
        # 最近的快照在 0，差 240s >> 15s 容差
        current = _make_oi("BTCUSDT", receive_time=540000, oi="120.0")
        change = lookup.compute_change("BTCUSDT", current, lookback_ms=300_000)

        assert change is None  # unavailable，不是取 9m 前

    def test_does_not_fallback_to_older_data(self):
        """不得回退取更旧数据。"""
        lookup = OILookup(default_tolerance_ms=1000)

        # 有 1m 前和 9m 前的快照
        lookup.add_snapshot(_make_oi("BTCUSDT", receive_time=0, oi="100.0"))
        lookup.add_snapshot(_make_oi("BTCUSDT", receive_time=480000, oi="110.0"))

        # 需要 5m 前的快照，target=300000
        # 1m 前的在 480000，差 180s >> 1s 容差
        # 9m 前的在 0，差 300s >> 1s 容差
        # → unavailable，不能取 9m 前的
        current = _make_oi("BTCUSDT", receive_time=600000, oi="120.0")
        change = lookup.compute_change("BTCUSDT", current, lookback_ms=300_000)
        assert change is None


# ────────────────────────────────────────────────────────────────────
# 场景 4：429 + Retry-After → 全局退避，不各模块重试
# ────────────────────────────────────────────────────────────────────


class TestScenario4RateLimit429:
    """429 返回 Retry-After → 全局限流按策略退避；不让其他模块各自重试。"""

    async def test_429_global_backoff_no_storm(self):
        """RateLimiter 集中处理 429，不产生 retry storm。"""
        config = RateLimiterConfig(
            initial_backoff_ms=10,
            circuit_breaker_threshold=100,  # 高阈值避免熔断
        )
        limiter = RateLimiter(config=config)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp_429 = MagicMock(spec=httpx.Response)
        mock_resp_429.status_code = 429
        mock_resp_429.headers = httpx.Headers({"Retry-After": "0.01"})

        mock_resp_200 = MagicMock(spec=httpx.Response)
        mock_resp_200.status_code = 200
        mock_resp_200.headers = httpx.Headers({})

        mock_client.request = AsyncMock(
            side_effect=[mock_resp_429, mock_resp_200]
        )
        limiter._client = mock_client
        limiter._owns_client = False

        resp = await limiter.request("GET", "https://example.com/api")
        # 退避后重试成功
        assert resp.status_code == 200
        assert limiter.state.total_429 == 1
        # 只重试了 1 次（集中退避，不是各模块各自重试）
        assert mock_client.request.call_count == 2


# ────────────────────────────────────────────────────────────────────
# 场景 5：关键数据 STALE → detector 无权进入 CONFIRMED
# ────────────────────────────────────────────────────────────────────


class TestScenario5StaleBlocksConfirm:
    """关键数据 STALE → confidence_state=UNKNOWN → detector 无权进入 CONFIRMED。"""

    def test_stale_aggtrade_blocks_confirm(self):
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(
            budget=FreshnessBudget(aggtrade_active_ms=5000),
            clock=clock,
        )
        tracker = ConfidenceTracker()

        # 注册所有关键流
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.register_stream("kline:BTCUSDT", "BTCUSDT", StreamType.KLINE)
        wd.register_stream("oi_poller:BTCUSDT", "BTCUSDT", StreamType.OI_POLLER)

        # 全部健康
        for s in ["aggTrade:BTCUSDT", "kline:BTCUSDT", "oi_poller:BTCUSDT"]:
            wd.mark_connected(s, True)
            wd.record_event(s, receive_time=1000)

        clock.set(2000)
        statuses = wd.check_all()
        tracker.update("BTCUSDT", statuses)
        assert tracker.can_confirm("BTCUSDT") is True

        # aggTrade 变 STALE（30s 无数据）
        clock.set(31_000)
        statuses = wd.check_all()
        tracker.update("BTCUSDT", statuses)

        # confidence 应为 UNKNOWN → 禁止 CONFIRMED
        assert tracker.can_confirm("BTCUSDT") is False
        assert tracker.get("BTCUSDT").value == "UNKNOWN"


# ────────────────────────────────────────────────────────────────────
# 场景 6：WS 半死（TCP open 无数据）→ budget 内降级 STALE
# ────────────────────────────────────────────────────────────────────


class TestScenario6WSHalfDead:
    """WS 半死状态：TCP open 但无心跳/数据 → freshness watchdog 在 budget 内降级 STALE。"""

    def test_half_dead_detected_within_budget(self):
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(
            budget=FreshnessBudget(aggtrade_active_ms=5000),
            clock=clock,
        )
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)

        # WS 连接成功
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.record_event("aggTrade:BTCUSDT", event_time=1000, receive_time=1000)

        # WS 仍 open，但 6s 无数据（超过 5s budget）
        clock.set(7_000)

        hs = wd.check_health("aggTrade:BTCUSDT")
        assert hs.connected is True       # TCP 仍 open
        assert hs.status == HealthLevel.STALE  # 但被 watchdog 降级
        assert hs.stale_seconds is not None
        assert hs.stale_seconds >= 6

    def test_connected_not_healthy_explicit(self):
        """明确验证 connected=True 但 healthy=False。"""
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(
            budget=FreshnessBudget(aggtrade_active_ms=5000),
            clock=clock,
        )
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.record_event("aggTrade:BTCUSDT", receive_time=1000)

        clock.set(10_000)
        hs = wd.check_health("aggTrade:BTCUSDT")
        # 核心断言：connected ≠ healthy
        assert hs.connected is True
        assert hs.status != HealthLevel.OK


# ────────────────────────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────────────────────────


def _make_trade(symbol: str, trade_id: int) -> TradeEvent:
    return TradeEvent(
        symbol=symbol,
        exchange="binance",
        trade_id=trade_id,
        event_time=trade_id * 1000,
        receive_time=trade_id * 1000,
        price=Decimal("50000"),
        qty=Decimal("0.1"),
        quote_notional=Decimal("5000"),
        aggressor_side=AggressorSide.BUY,
        is_maker=False,
    )


def _make_oi(symbol: str, receive_time: int, oi: str) -> OpenInterestSnapshot:
    return OpenInterestSnapshot(
        symbol=symbol,
        exchange="binance",
        event_time=receive_time,
        receive_time=receive_time,
        open_interest=Decimal(oi),
        source="binance_rest_openinterest",
        freshness_ms=0,
    )
