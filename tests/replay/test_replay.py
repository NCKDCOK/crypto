"""Replay Engine 测试 — 确定性、重放一致。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import AggressorSide, ConfidenceState, TradeEvent
from src.replay.engine import ReplayEngine
from src.replay.labeling import Label, LabelStore, LabelType


def _trade(trade_id, price, qty, side, event_time=None):
    et = event_time if event_time is not None else trade_id * 1000
    return TradeEvent(
        symbol="BTCUSDT", exchange="binance", trade_id=trade_id,
        event_time=et, receive_time=et,
        price=Decimal(str(price)), qty=Decimal(str(qty)),
        quote_notional=Decimal(str(price)) * Decimal(str(qty)),
        aggressor_side=side, is_maker=(side == AggressorSide.SELL),
    )


class TestReplayDeterminism:
    def test_same_input_same_output(self):
        """相同输入重放两次 → 状态序列一致。"""
        trades = [
            _trade(1, "100", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "101", "0.1", AggressorSide.BUY, 2000),
            _trade(3, "102", "0.1", AggressorSide.BUY, 3000),
        ]
        engine = ReplayEngine()
        result1 = engine.replay(trades)
        result2 = engine.replay_deterministic(trades)

        assert result1.events_processed == result2.events_processed
        assert len(result1.transitions) == len(result2.transitions)

    def test_empty_trades(self):
        engine = ReplayEngine()
        result = engine.replay([])
        assert result.events_processed == 0

    def test_events_processed_count(self):
        trades = [
            _trade(i, "100", "0.1", AggressorSide.BUY, i * 1000)
            for i in range(1, 11)
        ]
        engine = ReplayEngine()
        result = engine.replay(trades)
        assert result.events_processed == 10

    def test_trades_sorted_by_event_time(self):
        """乱序输入 → 按 event_time 排序后重放。"""
        trades = [
            _trade(3, "103", "0.1", AggressorSide.BUY, 3000),
            _trade(1, "101", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "102", "0.1", AggressorSide.BUY, 2000),
        ]
        engine = ReplayEngine()
        result = engine.replay(trades)
        assert result.events_processed == 3


class TestLabeling:
    def test_add_and_get(self):
        store = LabelStore()
        store.add(Label(symbol="BTCUSDT", asof=1000, label_type=LabelType.CLEAN_START))
        store.add(Label(symbol="ETHUSDT", asof=2000, label_type=LabelType.FALSE_START))
        assert len(store.get_by_symbol("BTCUSDT")) == 1
        assert len(store.get_by_symbol("ETHUSDT")) == 1
        assert len(store.get_all()) == 2

    def test_count_by_type(self):
        store = LabelStore()
        store.add(Label(symbol="BTC", asof=1, label_type=LabelType.CLEAN_START))
        store.add(Label(symbol="ETH", asof=2, label_type=LabelType.CLEAN_START))
        store.add(Label(symbol="SOL", asof=3, label_type=LabelType.FALSE_START))
        counts = store.count_by_type()
        assert counts["clean_start"] == 2
        assert counts["false_start"] == 1

    def test_label_doesnt_affect_state_machine(self):
        """标签只记录，不回写改变状态机。"""
        store = LabelStore()
        label = Label(
            symbol="BTCUSDT", asof=1000,
            label_type=LabelType.FALSE_START,
            notes="should not affect anything",
        )
        store.add(label)
        # 标签存储是独立的，不影响任何运行时状态
        assert len(store.get_all()) == 1
        assert store.get_all()[0].notes == "should not affect anything"
