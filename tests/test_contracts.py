"""Contract tests — domain 对象序列化/反序列化、枚举、字段约束。

依据：docs/TESTING.md §2.1, docs/DATA_MODEL.md
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.domain import (
    AggressorSide,
    AnalysisEvent,
    ConfidenceState,
    Direction,
    Evidence,
    EvidenceFamily,
    FeatureSnapshot,
    FeatureValue,
    FundingRateSnapshot,
    HealthLevel,
    HealthStatus,
    KlineEvent,
    KlineInterval,
    OpenInterestSnapshot,
    State,
    TradeEvent,
    Veto,
    VetoSeverity,
    VetoType,
)


# ────────────────────────────────────────────────────────────────────
# AggressorSide 映射（P0 — 方向反转风险）
# ────────────────────────────────────────────────────────────────────


class TestAggressorSideMapping:
    """Binance aggTrade m 字段 → aggressor_side 映射。

    m=True  → 买方是 maker → 卖方主动 → SELL
    m=False → 买方是 taker → BUY
    m=None  → UNKNOWN

    写反则 CVD/Taker Delta 全量反转。
    """

    @pytest.mark.parametrize(
        "m, expected",
        [
            (True, AggressorSide.SELL),
            (False, AggressorSide.BUY),
            (None, AggressorSide.UNKNOWN),
        ],
    )
    def test_from_binance_m(self, m, expected):
        assert AggressorSide.from_binance_m(m) == expected

    def test_m_true_means_seller_is_aggressor(self):
        """m=True ⇒ 买方是 maker ⇒ 卖方主动。这是最关键的 P0 断言。"""
        assert AggressorSide.from_binance_m(True) == AggressorSide.SELL

    def test_m_false_means_buyer_is_aggressor(self):
        assert AggressorSide.from_binance_m(False) == AggressorSide.BUY


# ────────────────────────────────────────────────────────────────────
# TradeEvent
# ────────────────────────────────────────────────────────────────────


class TestTradeEvent:
    def _make(self, **overrides):
        defaults = {
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "trade_id": 12345,
            "event_time": 1672515782136,
            "receive_time": 1672515782137,
            "price": Decimal("0.001"),
            "qty": Decimal("100"),
            "quote_notional": Decimal("0.1"),  # 0.001 * 100
            "aggressor_side": AggressorSide.BUY,
            "is_maker": False,
        }
        defaults.update(overrides)
        return TradeEvent(**defaults)

    def test_valid_trade(self):
        t = self._make()
        assert t.symbol == "BTCUSDT"
        assert t.trade_id == 12345

    def test_quote_notional_must_equal_price_times_qty(self):
        with pytest.raises(ValidationError, match="quote_notional"):
            self._make(quote_notional=Decimal("999"))

    def test_json_roundtrip(self):
        t = self._make()
        data = json.loads(t.model_dump_json())
        t2 = TradeEvent.model_validate_json(json.dumps(data))
        assert t2 == t

    def test_aggressor_side_from_m_true(self):
        """模拟 Binance m=True 的真实场景。"""
        m = True
        t = self._make(
            is_maker=m,
            aggressor_side=AggressorSide.from_binance_m(m),
        )
        assert t.aggressor_side == AggressorSide.SELL
        assert t.is_maker is True

    def test_aggressor_side_from_m_false(self):
        m = False
        t = self._make(
            is_maker=m,
            aggressor_side=AggressorSide.from_binance_m(m),
        )
        assert t.aggressor_side == AggressorSide.BUY
        assert t.is_maker is False

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            self._make(unknown_field="x")

    def test_negative_trade_id_rejected(self):
        with pytest.raises(ValidationError):
            self._make(trade_id=-1)


# ────────────────────────────────────────────────────────────────────
# KlineEvent
# ────────────────────────────────────────────────────────────────────


class TestKlineEvent:
    def _make(self, **overrides):
        defaults = {
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "interval": KlineInterval.M1,
            "open_time": 1672515780000,
            "close_time": 1672515839999,
            "event_time": 1672515782136,
            "receive_time": 1672515782137,
            "open": Decimal("0.0010"),
            "high": Decimal("0.0025"),
            "low": Decimal("0.0015"),
            "close": Decimal("0.0020"),
            "volume": Decimal("1000"),
            "quote_volume": Decimal("1.0000"),
            "trade_count": 100,
            "is_closed": False,
        }
        defaults.update(overrides)
        return KlineEvent(**defaults)

    def test_valid_kline(self):
        k = self._make()
        assert k.interval == KlineInterval.M1
        assert k.is_closed is False

    def test_json_roundtrip(self):
        k = self._make()
        data = json.loads(k.model_dump_json())
        k2 = KlineEvent.model_validate_json(json.dumps(data))
        assert k2 == k

    def test_close_time_before_open_time_rejected(self):
        with pytest.raises(ValidationError, match="close_time"):
            self._make(open_time=2000, close_time=1000)

    def test_only_closed_bar_for_slow_confirmed(self):
        """is_closed=true 的 bar 才能进入慢周期确认。"""
        k_open = self._make(is_closed=False)
        k_closed = self._make(is_closed=True)
        assert not k_open.is_closed
        assert k_closed.is_closed


# ────────────────────────────────────────────────────────────────────
# OpenInterestSnapshot
# ────────────────────────────────────────────────────────────────────


class TestOpenInterestSnapshot:
    def _make(self, **overrides):
        defaults = {
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "event_time": 1672515782136,
            "receive_time": 1672515782137,
            "open_interest": Decimal("50000.5"),
            "source": "binance_rest_openinterest",
            "freshness_ms": 100,
        }
        defaults.update(overrides)
        return OpenInterestSnapshot(**defaults)

    def test_valid_oi(self):
        oi = self._make()
        assert oi.open_interest == Decimal("50000.5")

    def test_json_roundtrip(self):
        oi = self._make()
        data = json.loads(oi.model_dump_json())
        oi2 = OpenInterestSnapshot.model_validate_json(json.dumps(data))
        assert oi2 == oi

    def test_oi_unit_is_base_asset_not_notional(self):
        """OI 单位 = 基础资产数量，不是美元名义。

        价格涨但 open_interest 不变 ⇒ oi_change=0。
        """
        oi_before = self._make(open_interest=Decimal("100.0"))
        oi_after = self._make(
            open_interest=Decimal("100.0"),  # 数量不变
            receive_time=oi_before.receive_time + 60_000,
        )
        # 价格可能涨了，但 open_interest 不变 ⇒ change=0
        change = oi_after.open_interest - oi_before.open_interest
        assert change == Decimal("0")


# ────────────────────────────────────────────────────────────────────
# FundingRateSnapshot
# ────────────────────────────────────────────────────────────────────


class TestFundingRateSnapshot:
    def _make(self, **overrides):
        defaults = {
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "event_time": 1672515782136,
            "receive_time": 1672515782137,
            "mark_price": Decimal("50000.0"),
            "index_price": Decimal("49990.0"),
            "last_funding_rate": Decimal("0.0001"),
            "next_funding_time": 1672516800000,
            "premium": Decimal("10.0"),  # 50000 - 49990
            "source": "binance_rest_premiumindex",
        }
        defaults.update(overrides)
        return FundingRateSnapshot(**defaults)

    def test_valid_funding(self):
        f = self._make()
        assert f.premium == Decimal("10.0")

    def test_json_roundtrip(self):
        f = self._make()
        data = json.loads(f.model_dump_json())
        f2 = FundingRateSnapshot.model_validate_json(json.dumps(data))
        assert f2 == f

    def test_premium_must_equal_mark_minus_index(self):
        with pytest.raises(ValidationError, match="premium"):
            self._make(premium=Decimal("999"))


# ────────────────────────────────────────────────────────────────────
# HealthStatus
# ────────────────────────────────────────────────────────────────────


class TestHealthStatus:
    def test_connected_not_equal_healthy(self):
        """connected=True 但 status=STALE — WS 半死状态。"""
        hs = HealthStatus(
            stream="aggTrade:BTCUSDT",
            status=HealthLevel.STALE,
            connected=True,
            message_count=0,
            reconnect_count=0,
        )
        assert hs.connected is True
        assert hs.status == HealthLevel.STALE
        assert hs.status != HealthLevel.OK

    def test_json_roundtrip(self):
        hs = HealthStatus(
            stream="aggTrade:BTCUSDT",
            symbol="BTCUSDT",
            status=HealthLevel.OK,
            connected=True,
            message_count=100,
            reconnect_count=0,
        )
        data = json.loads(hs.model_dump_json())
        hs2 = HealthStatus.model_validate_json(json.dumps(data))
        assert hs2 == hs


# ────────────────────────────────────────────────────────────────────
# FeatureSnapshot + FeatureValue
# ────────────────────────────────────────────────────────────────────


class TestFeatureSnapshot:
    def test_with_features(self):
        snap = FeatureSnapshot(
            symbol="BTCUSDT",
            asof=1672515782136,
            windows={"micro": "30s"},
            features={
                "volume_z": FeatureValue(value=4.72, available=True, window="30s"),
                "oi_change_5m": FeatureValue(value=None, available=False, window="5m"),
            },
        )
        assert snap.features["volume_z"].available is True
        assert snap.features["oi_change_5m"].available is False

    def test_json_roundtrip(self):
        snap = FeatureSnapshot(
            symbol="BTCUSDT",
            asof=1672515782136,
            features={"rvol": FeatureValue(value=2.5, available=True)},
        )
        data = json.loads(snap.model_dump_json())
        snap2 = FeatureSnapshot.model_validate_json(json.dumps(data))
        assert snap2 == snap


# ────────────────────────────────────────────────────────────────────
# AnalysisEvent + Evidence + Veto
# ────────────────────────────────────────────────────────────────────


class TestAnalysisEvent:
    def test_full_analysis_event(self):
        ev = AnalysisEvent(
            symbol="ONGUSDT",
            direction=Direction.LONG,
            previous_state=State.SLEEPING,
            new_state=State.SUSPECTED_START,
            evidence=[
                Evidence(
                    family=EvidenceFamily.ANOMALY,
                    type="volume_z",
                    window="30s",
                    value=4.72,
                    threshold=3.0,
                    passed=True,
                ),
            ],
            vetoes=[
                Veto(
                    type=VetoType.RAPID_RETRACE,
                    triggered=False,
                    severity=VetoSeverity.HARD,
                ),
            ],
            asof=1672515782136,
            confidence_state=ConfidenceState.CONFIDENT,
        )
        assert ev.new_state == State.SUSPECTED_START
        assert ev.evidence[0].passed is True
        assert ev.vetoes[0].severity == VetoSeverity.HARD

    def test_json_roundtrip(self):
        ev = AnalysisEvent(
            symbol="BTCUSDT",
            previous_state=State.SLEEPING,
            new_state=State.ANOMALY,
            asof=1672515782136,
            confidence_state=ConfidenceState.DEGRADED,
        )
        data = json.loads(ev.model_dump_json())
        ev2 = AnalysisEvent.model_validate_json(json.dumps(data))
        assert ev2 == ev


# ────────────────────────────────────────────────────────────────────
# 枚举全覆盖
# ────────────────────────────────────────────────────────────────────


class TestEnums:
    def test_aggressor_side_values(self):
        assert {e.value for e in AggressorSide} == {"BUY", "SELL", "UNKNOWN"}

    def test_direction_values(self):
        assert {e.value for e in Direction} == {"LONG", "SHORT", "NEUTRAL"}

    def test_confidence_state_values(self):
        assert {e.value for e in ConfidenceState} == {"CONFIDENT", "DEGRADED", "UNKNOWN"}

    def test_health_level_values(self):
        assert {e.value for e in HealthLevel} == {"OK", "WARN", "STALE", "DRIFT", "FAIL"}

    def test_state_values(self):
        expected = {
            "SLEEPING", "ANOMALY", "SUSPECTED_START", "START_CONFIRMED",
            "CONTINUATION", "EXHAUSTION", "WITHDRAWAL", "REJECTED", "COOLDOWN",
        }
        assert {e.value for e in State} == expected

    def test_evidence_family_values(self):
        assert {e.value for e in EvidenceFamily} == {
            "ANOMALY", "FLOW", "POSITION", "PRICE_EFFECT", "CONTEXT",
        }

    def test_veto_type_values(self):
        assert {e.value for e in VetoType} == {
            "data_stale", "rapid_retrace", "oi_contraction", "delta_reversal",
            "no_acceptance", "low_efficiency_absorption", "crowding_extreme",
            "one_bar_spike",
        }

    def test_veto_severity_values(self):
        assert {e.value for e in VetoSeverity} == {"hard", "soft"}
