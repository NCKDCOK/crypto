"""§66.5 Entry Revalidation — 到 Zone 必须二次验证，撤资/翻转/stale 取消。"""

from __future__ import annotations

import pytest

from src.simulation import EntryRevalidationEngine, RevalidationResult


def _engine(**kw) -> EntryRevalidationEngine:
    return EntryRevalidationEngine(**kw)


class TestRevalidationPass:
    def test_all_checks_pass(self, good_ctx, snapshot_dict):
        res = _engine().evaluate(good_ctx, snapshot_dict, now_ms=0)
        assert res.passed is True
        assert res.passed_checks == len(res.checks) == 11

    def test_check_names_cover_spec(self, good_ctx, snapshot_dict):
        res = _engine().evaluate(good_ctx, snapshot_dict, now_ms=0)
        names = {c.name for c in res.checks}
        assert {"data_health", "state_legal", "setup_alive", "breakout_alive",
                "withdrawal", "direction_stable", "oi", "cvd", "spot_perp",
                "regime_stable", "pump_risk"} <= names


class TestRevalidationReject:
    """§66.5：任何一项不通过 → 取消，并记录 fail_reason。"""

    def test_withdrawal_triggered_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, withdrawal_active=True)
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        assert res.fail_reason and "Withdrawal" in res.fail_reason

    def test_direction_flip_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, direction="SHORT")
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        assert "方向翻转" in res.fail_reason

    def test_stale_data_no_entry(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, data_age_ms=120_000, data_confidence=90.0)
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        assert res.fail_reason and "stale" in res.fail_reason.lower()

    def test_low_data_confidence_no_entry(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, data_confidence=60.0)
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False

    def test_unknown_confidence_no_entry(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, confidence_state="UNKNOWN")
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False

    def test_state_leaving_formal_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, state="EXHAUSTION")
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        failed = next(c for c in res.checks if not c.passed)
        assert failed.name == "state_legal"

    def test_breakout_failed_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, breakout={"close_back_inside": True})
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        failed = next(c for c in res.checks if not c.passed)
        assert failed.name == "breakout_alive"

    def test_structure_failed_breakout_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, structure={"failed_breakout": True})
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        failed = next(c for c in res.checks if not c.passed)
        assert failed.name == "breakout_alive"

    def test_oi_reversal_cancels_long(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, features={"oi_change_5m": -0.20, "cvd_slope_z": 0.5})
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        assert res.fail_reason and "OI" in res.fail_reason

    def test_cvd_reversal_cancels_long(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, features={"oi_change_5m": 0.02, "cvd_slope_z": -3.5})
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        assert res.fail_reason and "CVD" in res.fail_reason

    def test_spot_perp_leverage_dominant_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, spot_perp={"classification": "leverage_dominant"})
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False

    def test_risk_off_regime_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, regime={"regime": "PANIC"})
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False

    def test_pump_risk_high_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, pump_risk=90.0)
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False

    def test_setup_invalid_set_cancels(self, good_ctx, snapshot_dict):
        """Setup 进入失效集合（NONE/DISTRIBUTION/PUMP_RISK）→ 取消。"""
        ctx = dict(good_ctx, setup_type="NONE")
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False

    def test_direction_missing_cancels(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, direction=None)
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False

    def test_first_failure_recorded_as_reason(self, good_ctx, snapshot_dict):
        """失败项即 fail_reason，且 passed_checks < total。"""
        ctx = dict(good_ctx, withdrawal_active=True, pump_risk=99.0)
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is False
        assert res.passed_checks < len(res.checks)


class TestSetupFamilyInterchange:
    """Setup 家族内互转（TREND_CONTINUATION ↔ RETEST_REIGNITION）不算失效。"""

    def test_family_switch_keeps_passing(self, good_ctx, snapshot_dict):
        ctx = dict(good_ctx, setup_type="RETEST_REIGNITION")
        res = _engine().evaluate(ctx, snapshot_dict, now_ms=0)
        assert res.passed is True
        assert res.to_dict()["fail_reason"] is None


class TestToDict:
    def test_result_serializable(self, good_ctx, snapshot_dict):
        res = _engine().evaluate(good_ctx, snapshot_dict, now_ms=0)
        d = res.to_dict()
        assert d["passed"] is True
        assert d["passed_checks"] == 11
        assert d["total_checks"] == 11
        assert isinstance(d["checks"], list)