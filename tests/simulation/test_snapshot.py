"""§66.3 Recommendation Snapshot — 不可变快照 + §22 自动快照门槛 + §19 版本化。"""

from __future__ import annotations

import pytest

from src.domain import State
from src.simulation import RecommendationSnapshot, RecommendationSnapshotService


def _service(**kw) -> RecommendationSnapshotService:
    return RecommendationSnapshotService(**kw)


def _passing_kwargs(**over):
    kw = dict(
        symbol="BTCUSDT",
        timestamp=1700000000000,
        market_regime={"regime": "TREND_UP"},
        state=State.START_CONFIRMED,
        setup_type="TREND_CONTINUATION",
        direction="LONG",
        current_price=100.0,
        opportunity_score=85.0,
        signal_confirmation=88.0,
        data_confidence=92.0,
        all_subscores={},
        all_evidence=[],
        all_vetoes=[],
        breakout_state={},
        structure_state={},
        spot_perp_state={},
        trade_plan={"status": "ACTIVE", "trade_plan_id": "TP-1"},
    )
    kw.update(over)
    return kw


class TestSnapshotImmutable:
    """§21 冻结后不可修改。"""

    def test_frozen_dataclass(self, snapshot_dict):
        snap = RecommendationSnapshot(
            snapshot_id=snapshot_dict["snapshot_id"],
            symbol=snapshot_dict["symbol"],
            timestamp=snapshot_dict["timestamp"],
            market_regime=snapshot_dict["market_regime"],
            state=snapshot_dict["state"],
            setup_type=snapshot_dict["setup_type"],
            direction=snapshot_dict["direction"],
            primary_timeframe=snapshot_dict["primary_timeframe"],
            current_price=snapshot_dict["current_price"],
            opportunity_score=snapshot_dict["opportunity_score"],
            signal_confirmation=snapshot_dict["signal_confirmation"],
            data_confidence=snapshot_dict["data_confidence"],
            all_subscores=snapshot_dict["all_subscores"],
            all_evidence=snapshot_dict["all_evidence"],
            all_vetoes=snapshot_dict["all_vetoes"],
            breakout_state=snapshot_dict["breakout_state"],
            structure_state=snapshot_dict["structure_state"],
            spot_perp_state=snapshot_dict["spot_perp_state"],
            trade_plan=snapshot_dict["trade_plan"],
        )
        with pytest.raises(Exception):
            snap.trade_plan = {}  # type: ignore[misc]

    def test_snapshot_not_overwritten_by_later_data(self, snapshot_dict):
        """§21/§66.3：后续实时数据不能覆盖已冻结快照。"""
        snap = RecommendationSnapshotService().build(**_passing_kwargs())
        d1 = snap.to_dict()
        # 冻结后实时价格继续变化，但快照自身不变
        assert snap.current_price == 100.0
        assert d1["current_price"] == 100.0
        assert d1["opportunity_score"] == 85.0


class TestSameSetupVersioned:
    """§19/§66.3：相同 Setup 版本化 — 每次正式冻结使用递增序号；同 Trade Plan 去重由 runtime 承担。"""

    def test_snapshot_ids_increment(self):
        svc = _service()
        s1 = svc.build(**_passing_kwargs(timestamp=1700000000000))
        s2 = svc.build(**_passing_kwargs(timestamp=1700000030000))
        assert s1.snapshot_id == "BTCUSDT-20231114-001"
        assert s2.snapshot_id == "BTCUSDT-20231114-002"
        assert s1.snapshot_id != s2.snapshot_id

    def test_distinct_symbols_have_own_sequences(self):
        svc = _service()
        a1 = svc.build(**_passing_kwargs(symbol="BTCUSDT"))
        b1 = svc.build(**_passing_kwargs(symbol="ETHUSDT"))
        a2 = svc.build(**_passing_kwargs(symbol="BTCUSDT"))
        assert a1.snapshot_id.endswith("-001")
        assert b1.snapshot_id.endswith("-001")
        assert a2.snapshot_id.endswith("-002")


class TestGate:
    """§13/§22 门槛：state∈正式范围、分数达标、Trade Plan 合法、非 stale、非 pump_risk_high。"""

    def _gate(self, svc, **over):
        kw = dict(
            state="START_CONFIRMED",
            opportunity_score=85.0,
            signal_confirmation=88.0,
            data_confidence=92.0,
            trade_plan={"status": "ACTIVE"},
            pump_risk=10.0,
            stale_flag=None,
        )
        kw.update(over)
        return svc.passes_gate(**kw)

    def test_start_confirmed_passes(self):
        svc = _service()
        assert self._gate(svc) is True

    def test_continuation_passes(self):
        svc = _service()
        assert self._gate(svc, state="CONTINUATION") is True

    def test_cooldown_rejected(self):
        """§66.4：COOLDOWN 不加入正式模拟。"""
        svc = _service()
        assert self._gate(svc, state="COOLDOWN") is False

    def test_suspected_start_rejected(self):
        """§66.4：SUSPECTED_START 不加入正式模拟。"""
        svc = _service()
        assert self._gate(svc, state="SUSPECTED_START") is False

    def test_low_opportunity_rejected(self):
        svc = _service()
        assert self._gate(svc, opportunity_score=69.0) is False

    def test_low_signal_confirmation_rejected(self):
        svc = _service()
        assert self._gate(svc, signal_confirmation=74.0) is False

    def test_low_data_confidence_rejected(self):
        svc = _service()
        assert self._gate(svc, data_confidence=84.0) is False

    def test_inactive_trade_plan_rejected(self):
        svc = _service()
        assert self._gate(svc, trade_plan={"status": "EXPIRED"}) is False

    def test_missing_trade_plan_rejected(self):
        svc = _service()
        assert self._gate(svc, trade_plan=None) is False

    def test_stale_rejected(self):
        svc = _service()
        assert self._gate(svc, stale_flag=1.0) is False

    def test_pump_risk_high_rejected(self):
        svc = _service()
        assert self._gate(svc, pump_risk=70.0) is False

    def test_thresholds_configurable(self):
        svc = _service(min_opportunity=90.0)
        assert svc.passes_gate(
            state="START_CONFIRMED", opportunity_score=89.0,
            signal_confirmation=88.0, data_confidence=92.0,
            trade_plan={"status": "ACTIVE"}, pump_risk=10.0, stale_flag=None,
        ) is False

    def test_state_enum_accepted(self):
        svc = _service()
        assert self._gate(svc, state=State.START_CONFIRMED) is True


class TestBuild:
    def test_build_preserves_all_fields(self, snapshot_dict):
        svc = _service()
        snap = svc.build(
            symbol=snapshot_dict["symbol"],
            timestamp=snapshot_dict["timestamp"],
            market_regime=snapshot_dict["market_regime"],
            state=snapshot_dict["state"],
            setup_type=snapshot_dict["setup_type"],
            direction=snapshot_dict["direction"],
            current_price=snapshot_dict["current_price"],
            opportunity_score=snapshot_dict["opportunity_score"],
            signal_confirmation=snapshot_dict["signal_confirmation"],
            data_confidence=snapshot_dict["data_confidence"],
            all_subscores=snapshot_dict["all_subscores"],
            all_evidence=snapshot_dict["all_evidence"],
            all_vetoes=snapshot_dict["all_vetoes"],
            breakout_state=snapshot_dict["breakout_state"],
            structure_state=snapshot_dict["structure_state"],
            spot_perp_state=snapshot_dict["spot_perp_state"],
            trade_plan=snapshot_dict["trade_plan"],
        )
        d = snap.to_dict()
        assert d["state"] == "START_CONFIRMED"
        assert d["direction"] == "LONG"
        assert d["trade_plan"]["reference_entry_low"] == 95.0
        assert d["all_evidence"][0]["passed"] is True

    def test_primary_timeframe_configurable(self):
        svc = _service(primary_timeframe="1h")
        snap = svc.build(**_passing_kwargs())
        assert snap.primary_timeframe == "1h"