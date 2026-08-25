"""Dedup 校验测试 — trade_id 去重在 Feature Engine 入口前生效。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import AggressorSide, TradeEvent
from src.health.dedup import TradeDedupValidator


def _make_trade(symbol: str, trade_id: int, receive_time: int = 1000) -> TradeEvent:
    return TradeEvent(
        symbol=symbol,
        exchange="binance",
        trade_id=trade_id,
        event_time=receive_time,
        receive_time=receive_time,
        price=Decimal("50000"),
        qty=Decimal("0.1"),
        quote_notional=Decimal("5000"),
        aggressor_side=AggressorSide.BUY,
        is_maker=False,
    )


class TestTradeDedupValidator:
    def test_first_trade_accepted(self):
        v = TradeDedupValidator()
        assert v.validate(_make_trade("BTCUSDT", 100)) is True

    def test_duplicate_rejected(self):
        v = TradeDedupValidator()
        v.validate(_make_trade("BTCUSDT", 100))
        assert v.validate(_make_trade("BTCUSDT", 100)) is False

    def test_lower_trade_id_rejected(self):
        """trade_id ≤ 已见最大值 → 丢弃。"""
        v = TradeDedupValidator()
        v.validate(_make_trade("BTCUSDT", 100))
        assert v.validate(_make_trade("BTCUSDT", 99)) is False

    def test_higher_trade_id_accepted(self):
        v = TradeDedupValidator()
        v.validate(_make_trade("BTCUSDT", 100))
        assert v.validate(_make_trade("BTCUSDT", 101)) is True

    def test_per_symbol_independent(self):
        v = TradeDedupValidator()
        v.validate(_make_trade("BTCUSDT", 100))
        assert v.validate(_make_trade("ETHUSDT", 100)) is True

    def test_stats_tracked(self):
        v = TradeDedupValidator()
        v.validate(_make_trade("BTCUSDT", 100))  # accept
        v.validate(_make_trade("BTCUSDT", 100))  # drop
        v.validate(_make_trade("BTCUSDT", 101))  # accept
        assert v.stats.total_seen == 3
        assert v.stats.total_dropped == 1

    def test_reset(self):
        v = TradeDedupValidator()
        v.validate(_make_trade("BTCUSDT", 100))
        v.reset()
        # 重置后同样的 trade_id 可接受
        assert v.validate(_make_trade("BTCUSDT", 100)) is True

    def test_prevents_cvd_pollution(self):
        """模拟重连场景：重连后重复 trade 不应被接受。"""
        v = TradeDedupValidator()
        for tid in range(100, 106):
            assert v.validate(_make_trade("BTCUSDT", tid)) is True
        # 重连后重发
        for tid in range(103, 106):
            assert v.validate(_make_trade("BTCUSDT", tid)) is False
        assert v.validate(_make_trade("BTCUSDT", 106)) is True
