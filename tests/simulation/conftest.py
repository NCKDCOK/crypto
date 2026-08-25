"""P2 模拟验证共享 fixtures：合法快照 / 合法 ctx / 配置。"""

from __future__ import annotations

import pytest

from src.config import RankingConfig, SimulationConfig


def _trade_plan(**over):
    plan = {
        "status": "ACTIVE",
        "trade_plan_id": "TP-1",
        "version": 1,
        "reference_entry_low": 95.0,
        "reference_entry_high": 105.0,
        "tp1": 110.0,
        "tp2": 120.0,
        "tp3": 130.0,
        "invalidation_price": 90.0,
    }
    plan.update(over)
    return plan


@pytest.fixture
def snapshot_dict() -> dict:
    """一份过 §13/§22 门槛的正式推荐快照（§20 字段）。"""
    return {
        "snapshot_id": "BTCUSDT-20250101-001",
        "symbol": "BTCUSDT",
        "timestamp": 1700000000000,
        "market_regime": {"regime": "TREND_UP", "label": "趋势向上", "detail": "", "factors": {}},
        "state": "START_CONFIRMED",
        "setup_type": "TREND_CONTINUATION",
        "direction": "LONG",
        "primary_timeframe": "15m",
        "current_price": 100.0,
        "opportunity_score": 85.0,
        "signal_confirmation": 88.0,
        "data_confidence": 92.0,
        "all_subscores": {"opportunity": [{"name": "o1", "score": 85.0}]},
        "all_evidence": [{"family": "TREND", "type": "trend_up", "passed": True}],
        "all_vetoes": [{"type": "RAPID_RETRACE", "triggered": False}],
        "breakout_state": {"breakout_state": "breakout_confirmed"},
        "structure_state": {"structure_state": "healthy"},
        "spot_perp_state": {"classification": "spot_aligned"},
        "trade_plan": _trade_plan(),
    }


@pytest.fixture
def good_ctx() -> dict:
    """一份通过全部 §26 检查的实时 ctx。"""
    return {
        "price": 100.0,
        "state": "START_CONFIRMED",
        "setup_type": "TREND_CONTINUATION",
        "direction": "LONG",
        "confidence_state": "CONFIRMED",
        "data_confidence": 92.0,
        "data_age_ms": 500,
        "features": {"oi_change_5m": 0.02, "cvd_slope_z": 0.5, "spot_perp_agreement": 0.4},
        "breakout": {"breakout_state": "breakout_confirmed"},
        "structure": {"structure_state": "healthy"},
        "spot_perp": {"classification": "spot_aligned"},
        "regime": {"regime": "TREND_UP"},
        "pump_risk": 10.0,
        "distribution_risk": 20.0,
        "withdrawal_active": False,
        "invalidated": False,
    }


@pytest.fixture
def sim_cfg() -> SimulationConfig:
    return SimulationConfig()


@pytest.fixture
def rank_cfg() -> RankingConfig:
    return RankingConfig()