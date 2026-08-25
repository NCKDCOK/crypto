"""Breakout Lifecycle Engine 测试 — V1.2 §15。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import KlineEvent, KlineInterval
from src.engines.breakout_lifecycle import BreakoutLifecycleEngine


def _kline5m(open_price, close_price, open_time=1000, closed=True, high=None, low=None) -> KlineEvent:
    return KlineEvent(
        symbol="BTCUSDT", interval=KlineInterval.M5, open_time=open_time,
        close_time=open_time + 300_000, event_time=open_time, receive_time=open_time,
        open=Decimal(str(open_price)), high=Decimal(str(high or max(open_price, close_price))),
        low=Decimal(str(low or min(open_price, close_price))), close=Decimal(str(close_price)),
        volume=Decimal("10"), quote_volume=Decimal("100"), trade_count=100, is_closed=closed,
    )


class TestBreakoutConfirmation:
    def test_closed_bar_above_level_confirms_up(self):
        eng = BreakoutLifecycleEngine()
        # 5m 收盘 > 100 → 向上突破确认
        r = eng.update("BTCUSDT", 1000, breakout_level=100.0,
                       current_price=105.0, kline_5m=_kline5m(99, 105))
        assert r.breakout_confirmed is True
        assert r.breakout_direction == "up"

    def test_wick_only_not_confirmed_up(self):
        """最高价刺穿 level 但收盘回到 level 下方 → 不算向上突破（§15.1）。
        注意：收盘<level 会触发向下突破逻辑，所以测试收在 level 内但非收盘上方。"""
        eng = BreakoutLifecycleEngine()
        # close=100 == level（未站外）+ high 刺穿 → 未确认向上突破
        r = eng.update("BTCUSDT", 1000, breakout_level=100.0,
                       current_price=100.0, kline_5m=_kline5m(99, 100, high=106))
        # 收盘 = level，未站外 → 不确认
        assert r.breakout_confirmed is False or r.breakout_direction != "up"

    def test_unclosed_bar_not_confirmed(self):
        eng = BreakoutLifecycleEngine()
        r = eng.update("BTCUSDT", 1000, breakout_level=100.0,
                       current_price=105.0, kline_5m=_kline5m(99, 105, closed=False))
        assert r.breakout_confirmed is False

    def test_downward_breakout(self):
        eng = BreakoutLifecycleEngine()
        r = eng.update("BTCUSDT", 1000, breakout_level=100.0,
                       current_price=94.0, kline_5m=_kline5m(101, 94))
        assert r.breakout_confirmed is True
        assert r.breakout_direction == "down"


class TestBreakoutHold:
    def test_hold_when_above_level(self):
        eng = BreakoutLifecycleEngine()
        eng.update("BTCUSDT", 1000, breakout_level=100.0,
                   current_price=105.0, kline_5m=_kline5m(99, 105))
        r = eng.update("BTCUSDT", 2000, breakout_level=100.0, current_price=103.0)
        assert r.breakout_hold is True

    def test_close_back_inside_breaks_hold(self):
        eng = BreakoutLifecycleEngine()
        eng.update("BTCUSDT", 1000, breakout_level=100.0,
                   current_price=105.0, kline_5m=_kline5m(99, 105))
        r = eng.update("BTCUSDT", 2000, breakout_level=100.0, current_price=98.0)
        assert r.close_back_inside is True
        assert r.breakout_hold is False


class TestRetest:
    def test_retest_detected(self):
        eng = BreakoutLifecycleEngine()
        eng.update("BTCUSDT", 1000, breakout_level=100.0,
                   current_price=110.0, kline_5m=_kline5m(99, 110))
        # 回落到接近 100
        r = eng.update("BTCUSDT", 2000, breakout_level=100.0, current_price=101.5)
        assert r.retest_started is True
        assert r.retest_depth is not None

    def test_second_confirmation(self):
        eng = BreakoutLifecycleEngine()
        eng.update("BTCUSDT", 1000, breakout_level=100.0,
                   current_price=110.0, kline_5m=_kline5m(99, 110))
        # 回踩到 107（深度 (110-107)/(110-100)=0.3 健康）
        eng.update("BTCUSDT", 2000, breakout_level=100.0, current_price=107.0,
                   fv={"acceptance": 0.7, "oi_change_5m": 0.01, "signed_delta": 5000})
        r = eng.update("BTCUSDT", 3000, breakout_level=100.0, current_price=107.5,
                       fv={"acceptance": 0.8, "oi_change_5m": 0.02, "signed_delta": 8000})
        assert r.retest_confirmed is True


class TestStrongConfirm:
    def test_strong_confirm_all_aligned(self):
        eng = BreakoutLifecycleEngine()
        eng.update("BTCUSDT", 1000, breakout_level=100.0,
                   current_price=110.0, kline_5m=_kline5m(99, 110))
        r = eng.update("BTCUSDT", 2000, breakout_level=100.0, current_price=108.0,
                       context_15m=0.01, context_1h=0.005)
        assert r.strong_confirm is True
        assert r.confirmation_strength == "strong"

    def test_not_strong_when_1h_adverse(self):
        eng = BreakoutLifecycleEngine()
        eng.update("BTCUSDT", 1000, breakout_level=100.0,
                   current_price=110.0, kline_5m=_kline5m(99, 110))
        r = eng.update("BTCUSDT", 2000, breakout_level=100.0, current_price=108.0,
                       context_15m=0.01, context_1h=-0.01)
        assert r.strong_confirm is False
