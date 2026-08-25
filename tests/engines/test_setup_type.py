"""Setup Type Engine 测试 — V1.2 §14。"""

from __future__ import annotations

from src.domain import State
from src.engines.setup_type import SetupTypeEngine, SETUP_LABELS


def _eng() -> SetupTypeEngine:
    return SetupTypeEngine()


class TestSetupType:
    def test_pump_risk(self):
        eng = _eng()
        r = eng.compute(State.ANOMALY, "LONG", {"price_return_5m": 0.2})
        assert r.setup_type == "PUMP_RISK"

    def test_distribution(self):
        eng = _eng()
        r = eng.compute(State.EXHAUSTION, "LONG", {}, distribution_risk=75)
        assert r.setup_type == "DISTRIBUTION"

    def test_short_squeeze(self):
        eng = _eng()
        # LONG + Price↑ + Delta↑ + OI↓
        r = eng.compute(State.START_CONFIRMED, "LONG",
                        {"signed_delta": 10000, "oi_change_5m": -0.03, "price_return_5m": 0.02})
        assert r.setup_type == "SHORT_SQUEEZE"

    def test_long_liquidation(self):
        eng = _eng()
        # SHORT + Delta↓ + OI↓
        r = eng.compute(State.START_CONFIRMED, "SHORT",
                        {"signed_delta": -10000, "oi_change_5m": -0.03})
        assert r.setup_type == "LONG_LIQUIDATION"

    def test_accumulation(self):
        eng = _eng()
        r = eng.compute(State.ANOMALY, "LONG", {}, accumulation_score=80)
        assert r.setup_type == "ACCUMULATION"

    def test_breakout_start(self):
        eng = _eng()
        r = eng.compute(State.START_CONFIRMED, "LONG", {"acceptance": 0.8, "price_return_5m": 0.01})
        assert r.setup_type == "BREAKOUT_START"

    def test_trend_continuation(self):
        eng = _eng()
        r = eng.compute(State.CONTINUATION, "LONG", {"retrace_ratio": 0.6, "cvd_slope_z": 2.0})
        assert r.setup_type == "TREND_CONTINUATION"

    def test_retest_reignition(self):
        eng = _eng()
        r = eng.compute(State.CONTINUATION, "LONG",
                        {"retrace_ratio": 0.2, "cvd_slope_z": 2.0})
        assert r.setup_type == "RETEST_REIGNITION"

    def test_none_default(self):
        eng = _eng()
        r = eng.compute(State.SLEEPING, None, {})
        assert r.setup_type == "NONE"

    def test_all_labels_present(self):
        for k in ("ACCUMULATION", "BREAKOUT_START", "RETEST_REIGNITION", "TREND_CONTINUATION",
                  "SHORT_SQUEEZE", "LONG_LIQUIDATION", "OVERSOLD_REBOUND", "DISTRIBUTION",
                  "PUMP_RISK", "NONE"):
            assert k in SETUP_LABELS
