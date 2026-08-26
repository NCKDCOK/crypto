"""ShortSqueezeEngine 测试 — V1.4 §十三–§二十二（§四十二 Short Squeeze 组）。

覆盖计划要求的全部判定：
- 仅 Funding 负 → 不触发
- Funding 极负 + OI↑ + 价格强势下跌 → 不应误判轧空（不进 buildup）
- Funding 极负 + OI↑ + 下跌效率衰减 → buildup
- 5m 收盘突破 + Taker/CVD 确认 → trigger
- 价格↑ + OI↓ → squeeze acceleration 证据
- Funding 回归中性 + OI↓ + 效率衰减 → exhaustion
- §二十 分类：新增多头 vs 空头回补
"""

from __future__ import annotations

from src.engines.short_squeeze import ShortSqueezeEngine, SqueezePhase, SqueezeType


def _fv(**over) -> dict:
    """基础 feature dict（默认无信号）。"""
    base = {
        "funding": -0.0002, "funding_zscore": -1.5, "funding_percentile": 10.0,
        "premium": 0.0,
        "global_account_ls_ratio": None,
        "top_trader_account_ls_ratio": None,
        "top_trader_position_ls_ratio": None,
        "oi_change_pct_5m": 0.0,
        "signed_delta": 0.0, "delta_ratio": 0.0, "taker_buy_sell_ratio": 1.0,
        "cvd_slope_z": 0.0, "price_efficiency": 0.3, "price_return_5m": 0.0,
        "acceptance": 0.5, "retrace_ratio": 0.2, "volume_z": 0.0,
    }
    base.update(over)
    return base


def _bo(**over) -> dict:
    base = {"breakout_confirmed": False, "breakout_direction": None,
            "close_back_inside": False, "breakout_hold": False}
    base.update(over)
    return base


class TestShortCrowding:
    def test_negative_funding_only_no_trigger(self):
        """§四十二：仅 Funding 负（无 OI 扩张、无拥挤共振）→ 不触发。"""
        eng = ShortSqueezeEngine()
        r = eng.update("X", 0, _fv(funding_percentile=2.0, funding_zscore=-2.5), _bo())
        assert r.phase == SqueezePhase.NONE

    def test_short_crowding_entered_on_funding_extreme_oi_expansion(self):
        eng = ShortSqueezeEngine()
        r = eng.update("X", 0, _fv(
            funding_percentile=2.0, funding_zscore=-2.5,
            oi_change_pct_5m=0.03, global_account_ls_ratio=0.6,
        ), _bo())
        assert r.phase == SqueezePhase.SHORT_CROWDING
        assert r.short_crowding_score >= 60.0


class TestSqueezeBuildup:
    def test_strong_down_not_misjudged_as_buildup(self):
        """§四十二：Funding 极负 + OI↑ + 价格强势下跌 → 不应进 buildup。"""
        eng = ShortSqueezeEngine()
        eng.update("X", 0, _fv(
            funding_percentile=2.0, oi_change_pct_5m=0.03,
            global_account_ls_ratio=0.6,
        ), _bo())                                    # → SHORT_CROWDING
        r = eng.update("X", 1, _fv(
            funding_percentile=2.0, oi_change_pct_5m=0.03,
            global_account_ls_ratio=0.6,
            signed_delta=-50000, price_return_5m=-0.02,   # 强势下跌
        ), _bo())
        assert r.phase == SqueezePhase.SHORT_CROWDING   # 不进 buildup

    def test_downside_efficiency_collapse_enters_buildup(self):
        """§四十二：Funding 极负 + OI↑ + 下跌效率衰减（卖压大但价格不再跌）→ buildup。"""
        eng = ShortSqueezeEngine()
        eng.update("X", 0, _fv(
            funding_percentile=2.0, oi_change_pct_5m=0.03,
            global_account_ls_ratio=0.6,
        ), _bo())                                    # → SHORT_CROWDING
        r = eng.update("X", 1, _fv(
            funding_percentile=2.0, oi_change_pct_5m=0.03,
            global_account_ls_ratio=0.6,
            signed_delta=-50000, price_return_5m=-0.001,  # 卖压大但价格不再有效下跌
            acceptance=0.7,
        ), _bo())
        assert r.phase == SqueezePhase.SQUEEZE_BUILDUP


class TestSqueezeTrigger:
    def test_5m_breakout_taker_cvd_confirm_trigger(self):
        """§四十二：5m 收盘突破 + Taker/CVD 确认 → trigger。"""
        eng = ShortSqueezeEngine()
        eng.update("X", 0, _fv(
            funding_percentile=2.0, oi_change_pct_5m=0.03,
            global_account_ls_ratio=0.6,
        ), _bo())                                    # → SHORT_CROWDING
        eng.update("X", 1, _fv(
            funding_percentile=2.0, oi_change_pct_5m=0.03,
            global_account_ls_ratio=0.6,
            signed_delta=-50000, price_return_5m=-0.001, acceptance=0.7,
        ), _bo())                                    # → BUILDUP
        r = eng.update("X", 2, _fv(
            funding_percentile=2.0, oi_change_pct_5m=0.03,
            taker_buy_sell_ratio=1.5, cvd_slope_z=1.0,
            price_efficiency=0.6, price_return_5m=0.01,
        ), _bo(breakout_confirmed=True, breakout_direction="up"))
        assert r.phase == SqueezePhase.SQUEEZE_TRIGGER


class TestSqueezeAcceleration:
    def test_price_up_oi_down_acceleration(self):
        """§四十二：价格↑ + OI↓ → squeeze acceleration 证据。"""
        eng = ShortSqueezeEngine()
        eng.update("X", 0, _fv(funding_percentile=2.0, oi_change_pct_5m=0.03,
                                global_account_ls_ratio=0.6), _bo())
        eng.update("X", 1, _fv(funding_percentile=2.0, oi_change_pct_5m=0.03,
                                global_account_ls_ratio=0.6,
                                signed_delta=-50000, price_return_5m=-0.001,
                                acceptance=0.7), _bo())
        eng.update("X", 2, _fv(funding_percentile=2.0, oi_change_pct_5m=0.03,
                                taker_buy_sell_ratio=1.5, cvd_slope_z=1.0,
                                price_efficiency=0.6, price_return_5m=0.01),
                   _bo(breakout_confirmed=True, breakout_direction="up"))
        r = eng.update("X", 3, _fv(
            price_return_5m=0.02, oi_change_pct_5m=-0.01,   # 价格↑ OI↓
            taker_buy_sell_ratio=1.7, cvd_slope_z=1.2, price_efficiency=0.7,
        ), _bo(breakout_confirmed=True, breakout_direction="up"))
        assert r.phase == SqueezePhase.SQUEEZE_ACCELERATION
        assert r.squeeze_strength > 0


class TestSqueezeExhaustion:
    def test_funding_normalize_oi_down_efficiency_collapse_exhaustion(self):
        """§四十二：Funding 回归中性 + OI↓ + 效率衰减 → exhaustion。"""
        eng = ShortSqueezeEngine()
        eng.update("X", 0, _fv(funding_percentile=2.0, oi_change_pct_5m=0.03,
                                global_account_ls_ratio=0.6), _bo())
        eng.update("X", 1, _fv(funding_percentile=2.0, oi_change_pct_5m=0.03,
                                global_account_ls_ratio=0.6,
                                signed_delta=-50000, price_return_5m=-0.001,
                                acceptance=0.7), _bo())
        eng.update("X", 2, _fv(funding_percentile=2.0, oi_change_pct_5m=0.03,
                                taker_buy_sell_ratio=1.5, cvd_slope_z=1.0,
                                price_efficiency=0.6, price_return_5m=0.01),
                   _bo(breakout_confirmed=True, breakout_direction="up"))
        eng.update("X", 3, _fv(price_return_5m=0.02, oi_change_pct_5m=-0.01,
                                taker_buy_sell_ratio=1.7, cvd_slope_z=1.2,
                                price_efficiency=0.7),
                   _bo(breakout_confirmed=True, breakout_direction="up"))
        r = eng.update("X", 4, _fv(
            funding_percentile=60.0, funding=-0.00005,       # funding 回归中性
            oi_change_pct_5m=-0.01, taker_buy_sell_ratio=1.0,  # 主动买衰减
            cvd_slope_z=0.1, price_efficiency=0.1, price_return_5m=0.0,
        ), _bo(breakout_confirmed=True, breakout_direction="up"))
        assert r.phase == SqueezePhase.SQUEEZE_EXHAUSTION


class TestClassification:
    """§二十：区分新增多头上涨 vs 空头回补上涨。"""

    def test_classify_short_squeeze(self):
        eng = ShortSqueezeEngine()
        r = eng.update("X", 0, _fv(
            price_return_5m=0.02, oi_change_pct_5m=-0.01,
            signed_delta=50000, cvd_slope_z=1.0,
        ), _bo())
        assert r.squeeze_type == SqueezeType.SHORT_SQUEEZE

    def test_classify_new_long(self):
        eng = ShortSqueezeEngine()
        r = eng.update("X", 0, _fv(
            price_return_5m=0.02, oi_change_pct_5m=0.01,
            signed_delta=50000, cvd_slope_z=1.0,
        ), _bo())
        assert r.squeeze_type == SqueezeType.NEW_LONG

    def test_classify_none_when_no_rise(self):
        eng = ShortSqueezeEngine()
        r = eng.update("X", 0, _fv(price_return_5m=-0.01), _bo())
        assert r.squeeze_type == SqueezeType.NONE


class TestScores:
    def test_positioning_divergence(self):
        """§二十四：普通户偏空 + 大户偏多 → 分歧明显（仅 Evidence，非「庄家做多」）。"""
        eng = ShortSqueezeEngine()
        r = eng.update("X", 0, _fv(
            global_account_ls_ratio=0.58,
            top_trader_position_ls_ratio=1.41,
        ), _bo())
        assert r.positioning_divergence_score is not None
        assert r.positioning_divergence_score > 0

    def test_positioning_divergence_none_without_data(self):
        eng = ShortSqueezeEngine()
        r = eng.update("X", 0, _fv(), _bo())
        assert r.positioning_divergence_score is None

    def test_short_crowding_score_bounded_0_100(self):
        eng = ShortSqueezeEngine()
        r = eng.update("X", 0, _fv(
            funding_percentile=0.0, oi_change_pct_5m=0.10,
            global_account_ls_ratio=0.3,
        ), _bo())
        assert 0.0 <= r.short_crowding_score <= 100.0

    def test_to_dict_roundtrip(self):
        eng = ShortSqueezeEngine()
        d = eng.update("X", 0, _fv(), _bo()).to_dict()
        assert d["phase"] == "NONE"
        assert "short_crowding_score" in d
        assert "squeeze_strength" in d
