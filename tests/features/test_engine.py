"""Feature Engine 测试 — 多窗口 FeatureSnapshot 组装 + provenance。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import AggressorSide, OpenInterestSnapshot, TradeEvent
from src.features.engine import FeatureEngine


def _trade(trade_id, price, qty, side, receive_time=None):
    rt = receive_time if receive_time is not None else trade_id * 1000
    return TradeEvent(
        symbol="BTCUSDT", exchange="binance", trade_id=trade_id,
        event_time=rt, receive_time=rt,
        price=Decimal(str(price)), qty=Decimal(str(qty)),
        quote_notional=Decimal(str(price)) * Decimal(str(qty)),
        aggressor_side=side, is_maker=(side == AggressorSide.SELL),
    )


def _oi(receive_time, oi):
    return OpenInterestSnapshot(
        symbol="BTCUSDT", exchange="binance", event_time=receive_time,
        receive_time=receive_time, open_interest=Decimal(oi),
        source="binance_rest_openinterest", freshness_ms=0,
    )


class TestFeatureEngineSnapshot:
    def test_snapshot_has_provenance(self):
        """FeatureSnapshot 必须有 provenance。"""
        engine = FeatureEngine()
        engine.add_trade("BTCUSDT", _trade(1, "100", "0.1", AggressorSide.BUY, 1000))
        engine.add_trade("BTCUSDT", _trade(2, "101", "0.1", AggressorSide.BUY, 2000))
        snap = engine.compute_snapshot("BTCUSDT", 2000)
        assert snap.provenance is not None
        assert "volume" in snap.provenance
        assert "flow" in snap.provenance
        assert "efficiency" in snap.provenance

    def test_snapshot_has_features(self):
        engine = FeatureEngine()
        engine.add_trade("BTCUSDT", _trade(1, "100", "0.1", AggressorSide.BUY, 1000))
        engine.add_trade("BTCUSDT", _trade(2, "101", "0.1", AggressorSide.BUY, 2000))
        snap = engine.compute_snapshot("BTCUSDT", 2000)
        assert "rvol" in snap.features
        assert "volume_z" in snap.features
        assert "taker_delta" in snap.features
        assert "cvd" in snap.features
        assert "directional_efficiency" in snap.features
        assert "flow_impact" in snap.features
        # 多窗口 + 新增 catalog
        assert "price_return_5s" in snap.features
        assert "price_return_30s" in snap.features
        assert "oi_contracts" in snap.features
        assert "price_efficiency" in snap.features

    def test_empty_trades_features_none(self):
        """无成交 → 量类/效率特征为 None；CVD 初始 0。"""
        engine = FeatureEngine()
        snap = engine.compute_snapshot("BTCUSDT", 1000)
        assert snap.features["cvd"].value == 0.0
        assert snap.features["cvd"].available is True
        assert snap.features["taker_delta"].available is False

    def test_oi_features_in_snapshot(self):
        engine = FeatureEngine()
        engine.add_oi_snapshot(_oi(100000, "100.0"))
        engine.add_oi_snapshot(_oi(160000, "120.0"))
        snap = engine.compute_snapshot("BTCUSDT", 160000)
        assert "oi_change_1m" in snap.features
        assert snap.features["oi_change_1m"].available is True
        assert snap.features["oi_change_1m"].value == 20.0

    def test_oi_flat_change_zero(self):
        """价格变动但 OI 不变 → oi_change=0。"""
        engine = FeatureEngine()
        engine.add_oi_snapshot(_oi(100000, "100.0"))
        engine.add_oi_snapshot(_oi(160000, "100.0"))
        snap = engine.compute_snapshot("BTCUSDT", 160000)
        assert snap.features["oi_change_1m"].value == 0.0

    def test_provenance_traceable(self):
        """provenance 记录来源 stream。"""
        engine = FeatureEngine()
        engine.add_trade("BTCUSDT", _trade(1, "100", "0.1", AggressorSide.BUY, 1000))
        snap = engine.compute_snapshot("BTCUSDT", 1000)
        assert "source_streams" in snap.provenance["volume"]
        assert "aggTrade" in snap.provenance["volume"]["source_streams"]

    def test_cvd_accumulates_per_trade(self):
        """CVD 按每笔成交累积（每笔只计一次）。"""
        engine = FeatureEngine()
        engine.add_trade("BTCUSDT", _trade(1, "100", "0.1", AggressorSide.BUY, 1000))
        snap1 = engine.compute_snapshot("BTCUSDT", 1000)
        cvd1 = snap1.features["cvd"].value

        engine.add_trade("BTCUSDT", _trade(2, "101", "0.1", AggressorSide.BUY, 2000))
        snap2 = engine.compute_snapshot("BTCUSDT", 2000)
        cvd2 = snap2.features["cvd"].value
        assert cvd2 > cvd1  # 累积增加

    def test_cvd_not_double_counted_across_snapshots(self):
        """同一笔成交不会被重复计入 CVD（修复旧重叠窗口重复计数 bug）。"""
        engine = FeatureEngine()
        engine.add_trade("BTCUSDT", _trade(1, "100", "0.1", AggressorSide.BUY, 1000))
        s1 = engine.compute_snapshot("BTCUSDT", 1000)
        # 不新增成交，再次计算 → CVD 不应变化
        s2 = engine.compute_snapshot("BTCUSDT", 2000)
        assert s1.features["cvd"].value == s2.features["cvd"].value

    def test_multi_window_returns(self):
        """多窗口 price_return 各自计算。"""
        engine = FeatureEngine()
        # 5s 窗口内价格变化
        engine.add_trade("BTCUSDT", _trade(1, "100", "0.1", AggressorSide.BUY, 1000))
        engine.add_trade("BTCUSDT", _trade(2, "110", "0.1", AggressorSide.BUY, 2000))
        snap = engine.compute_snapshot("BTCUSDT", 2000)
        assert snap.features["price_return_5s"].available
        assert snap.features["price_return_5s"].value > 0
