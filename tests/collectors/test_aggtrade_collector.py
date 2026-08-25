"""aggTrade Collector 测试 — 解析、aggressor_side 映射、trade_id 去重。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.clock import TestClock
from src.collectors.aggtrade_collector import (
    AggTradeCollector,
    TradeDedup,
    parse_aggtrade_payload,
)
from src.domain import AggressorSide, TradeEvent

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _load_aggtrades() -> list[dict]:
    """加载 aggTrade fixture（每行一条 JSON）。"""
    trades = []
    with open(FIXTURES / "aggtrade_stream.jsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                trades.append(json.loads(line))
    return trades


class TestParseAggTradePayload:
    def test_parse_normal_buy(self):
        """m=false → BUY（买方是 taker）。"""
        payload = {
            "e": "aggTrade",
            "E": 1672515782136,
            "s": "BTCUSDT",
            "a": 12345,
            "p": "50000.00",
            "q": "0.100",
            "T": 1672515782130,
            "m": False,
        }
        event = parse_aggtrade_payload(payload, receive_time=1672515782137)
        assert event is not None
        assert event.symbol == "BTCUSDT"
        assert event.trade_id == 12345
        assert event.aggressor_side == AggressorSide.BUY
        assert event.is_maker is False
        assert event.price == Decimal("50000.00")
        assert event.qty == Decimal("0.100")
        assert event.quote_notional == Decimal("5000.0000")

    def test_parse_normal_sell(self):
        """m=true → SELL（买方是 maker，卖方主动）。"""
        payload = {
            "e": "aggTrade",
            "E": 1672515782136,
            "s": "BTCUSDT",
            "a": 12346,
            "p": "50001.00",
            "q": "0.200",
            "T": 1672515782135,
            "m": True,
        }
        event = parse_aggtrade_payload(payload, receive_time=1672515782137)
        assert event is not None
        assert event.aggressor_side == AggressorSide.SELL
        assert event.is_maker is True

    def test_parse_non_aggtrade_returns_none(self):
        payload = {"e": "kline", "E": 123}
        assert parse_aggtrade_payload(payload, receive_time=124) is None

    def test_quote_notional_calculated(self):
        """quote_notional = price × qty（本地计算）。"""
        payload = {
            "e": "aggTrade",
            "E": 1672515782136,
            "s": "ETHUSDT",
            "a": 999,
            "p": "3000.50",
            "q": "2.5",
            "T": 1672515782130,
            "m": False,
        }
        event = parse_aggtrade_payload(payload, receive_time=1672515782137)
        assert event is not None
        assert event.quote_notional == Decimal("3000.50") * Decimal("2.5")

    def test_parse_from_fixture(self):
        """从 fixture 文件解析。"""
        trades = _load_aggtrades()
        assert len(trades) == 4

        events = [
            parse_aggtrade_payload(t, receive_time=1672515782999)
            for t in trades
        ]
        assert all(e is not None for e in events)

        # 第一条 m=false → BUY
        assert events[0].aggressor_side == AggressorSide.BUY
        # 第二条 m=true → SELL
        assert events[1].aggressor_side == AggressorSide.SELL


class TestTradeDedup:
    def test_accept_new_trade(self):
        dedup = TradeDedup()
        assert dedup.should_accept("BTCUSDT", 100) is True

    def test_reject_duplicate_trade(self):
        """重连后 trade_id ≤ 已见最大值 → 丢弃。"""
        dedup = TradeDedup()
        dedup.should_accept("BTCUSDT", 100)
        assert dedup.should_accept("BTCUSDT", 100) is False
        assert dedup.should_accept("BTCUSDT", 99) is False  # 更小的也丢弃

    def test_accept_higher_trade(self):
        dedup = TradeDedup()
        dedup.should_accept("BTCUSDT", 100)
        assert dedup.should_accept("BTCUSDT", 101) is True

    def test_per_symbol_independent(self):
        """不同 symbol 的 trade_id 独立维护。"""
        dedup = TradeDedup()
        dedup.should_accept("BTCUSDT", 100)
        # ETHUSDT 的 100 不受 BTCUSDT 影响
        assert dedup.should_accept("ETHUSDT", 100) is True

    def test_get_max_trade_id(self):
        dedup = TradeDedup()
        dedup.should_accept("BTCUSDT", 42)
        assert dedup.get_max_trade_id("BTCUSDT") == 42
        assert dedup.get_max_trade_id("ETHUSDT") is None

    def test_dedup_prevents_cvd_pollution(self):
        """模拟重连场景：重连后重复 trade 不应被接受。"""
        dedup = TradeDedup()
        # 正常接收 trade_id 100-105
        for tid in range(100, 106):
            assert dedup.should_accept("BTCUSDT", tid) is True
        # 重连后重发 103-105 → 全部丢弃
        for tid in range(103, 106):
            assert dedup.should_accept("BTCUSDT", tid) is False
        # 新的 106 → 接受
        assert dedup.should_accept("BTCUSDT", 106) is True


class TestAggTradeCollectorDedup:
    """测试 collector 级别的去重集成。"""

    def test_collector_dedup_drops_duplicates(self):
        """collector 的 on_event 集成 TradeDedup，丢弃重复 trade。"""
        clock = TestClock(initial_ms=1672515782000)
        received: list[TradeEvent] = []

        def on_trade(event):
            received.append(event)

        collector = AggTradeCollector(
            symbols=["BTCUSDT"],
            clock=clock,
            on_trade=on_trade,
        )

        # 构造两个相同 trade_id 的 trade
        payload1 = {
            "e": "aggTrade", "E": 1672515782136, "s": "BTCUSDT",
            "a": 100, "p": "50000", "q": "0.1", "T": 1672515782130, "m": False,
        }
        payload2 = dict(payload1)  # 完全相同 trade_id

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            # 第一次接受
            e1 = collector.parse_payload("btcusdt@aggTrade", payload1)
            loop.run_until_complete(collector.on_event(e1))
            # 第二次丢弃
            e2 = collector.parse_payload("btcusdt@aggTrade", payload2)
            loop.run_until_complete(collector.on_event(e2))
        finally:
            loop.close()

        assert len(received) == 1
        assert received[0].trade_id == 100
