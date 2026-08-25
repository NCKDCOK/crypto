"""Flow 特征测试 — Taker Delta / CVD 方向正确性。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import AggressorSide, TradeEvent
from src.features.flow_features import CVDTracker, compute_flow_features, compute_taker_delta


def _trade(symbol, trade_id, price, qty, side, receive_time=None):
    rt = receive_time if receive_time is not None else trade_id * 1000
    return TradeEvent(
        symbol=symbol, exchange="binance", trade_id=trade_id,
        event_time=rt, receive_time=rt,
        price=Decimal(str(price)), qty=Decimal(str(qty)),
        quote_notional=Decimal(str(price)) * Decimal(str(qty)),
        aggressor_side=side, is_maker=(side == AggressorSide.SELL),
    )


class TestComputeTakerDelta:
    def test_buy_dominant(self):
        """主动买入多于卖出 → delta > 0。"""
        trades = [
            _trade("BTCUSDT", 1, "50000", "0.1", AggressorSide.BUY),
            _trade("BTCUSDT", 2, "50000", "0.1", AggressorSide.SELL),
            _trade("BTCUSDT", 3, "50000", "0.2", AggressorSide.BUY),
        ]
        delta = compute_taker_delta(trades)
        # buy: 0.1*50000 + 0.2*50000 = 15000
        # sell: 0.1*50000 = 5000
        # delta = 10000
        assert delta == 10000.0

    def test_sell_dominant(self):
        trades = [
            _trade("BTCUSDT", 1, "50000", "0.1", AggressorSide.SELL),
            _trade("BTCUSDT", 2, "50000", "0.1", AggressorSide.SELL),
        ]
        delta = compute_taker_delta(trades)
        assert delta == -10000.0

    def test_empty(self):
        assert compute_taker_delta([]) is None

    def test_m_true_means_sell(self):
        """验证 m=true→SELL 的方向正确性。

        aggressor_side=SELL 的 trade 应计入 sell_notional。
        """
        trades = [_trade("BTCUSDT", 1, "50000", "0.1", AggressorSide.SELL)]
        delta = compute_taker_delta(trades)
        # 只有 sell → delta = 0 - 5000 = -5000
        assert delta == -5000.0


class TestCVDTracker:
    def test_cumulative(self):
        """CVD 累积。"""
        tracker = CVDTracker()
        tracker.update("BTCUSDT", 100.0, 1000)
        tracker.update("BTCUSDT", 50.0, 2000)
        assert tracker.get_cvd("BTCUSDT") == 150.0

    def test_per_symbol(self):
        tracker = CVDTracker()
        tracker.update("BTCUSDT", 100.0, 1000)
        tracker.update("ETHUSDT", 200.0, 1000)
        assert tracker.get_cvd("BTCUSDT") == 100.0
        assert tracker.get_cvd("ETHUSDT") == 200.0

    def test_reset(self):
        tracker = CVDTracker()
        tracker.update("BTCUSDT", 100.0, 1000)
        tracker.reset("BTCUSDT")
        assert tracker.get_cvd("BTCUSDT") == 0.0

    def test_cvd_not_polluted_by_duplicates(self):
        """重连重复 trade 不污染 CVD — 如果去重层生效，同一 delta 不会被重复传入。"""
        tracker = CVDTracker()
        tracker.update("BTCUSDT", 100.0, 1000)
        # 正常情况：新的 delta
        tracker.update("BTCUSDT", 50.0, 2000)
        assert tracker.get_cvd("BTCUSDT") == 150.0
        # 如果去重层生效，不会再次传入 100.0
        # 模拟去重生效：只传入新 delta
        tracker.update("BTCUSDT", 30.0, 3000)
        assert tracker.get_cvd("BTCUSDT") == 180.0


class TestComputeFlowFeatures:
    def test_basic(self):
        trades = [
            _trade("BTCUSDT", 1, "50000", "0.1", AggressorSide.BUY, receive_time=1000),
            _trade("BTCUSDT", 2, "50000", "0.1", AggressorSide.BUY, receive_time=2000),
        ]
        tracker = CVDTracker()
        result = compute_flow_features("BTCUSDT", trades, tracker, 2000)
        assert result.taker_delta == 10000.0  # 2 * 0.1 * 50000
        assert result.cvd == 10000.0

    def test_empty_trades(self):
        tracker = CVDTracker()
        result = compute_flow_features("BTCUSDT", [], tracker, 1000)
        assert result.taker_delta is None
        assert result.cvd == 0.0


class TestTakerBS:
    """V1.3 §45：Taker B/S = 主动买名义额 / 主动卖名义额。"""

    def _result(self, trades):
        tracker = CVDTracker()
        return compute_flow_features("BTCUSDT", trades, tracker, 2000)

    def test_bs_ratio(self):
        """buy=15000, sell=5000 → B/S = 3.0。"""
        trades = [
            _trade("BTCUSDT", 1, "50000", "0.1", AggressorSide.BUY),
            _trade("BTCUSDT", 2, "50000", "0.1", AggressorSide.SELL),
            _trade("BTCUSDT", 3, "50000", "0.2", AggressorSide.BUY),
        ]
        result = self._result(trades)
        assert result.taker_bs == 3.0

    def test_bs_buy_only_none(self):
        """只有主动买（sell==0）→ None（避免除零），delta_ratio 仍为 1.0。"""
        trades = [_trade("BTCUSDT", 1, "50000", "0.1", AggressorSide.BUY)]
        result = self._result(trades)
        assert result.taker_bs is None
        assert result.delta_ratio == 1.0

    def test_bs_all_sell_zero(self):
        """buy==0, sell>0 → B/S = 0.0。"""
        trades = [_trade("BTCUSDT", 1, "50000", "0.1", AggressorSide.SELL)]
        result = self._result(trades)
        assert result.taker_bs == 0.0
        assert result.delta_ratio == -1.0

    def test_bs_empty(self):
        assert self._result([]).taker_bs is None
