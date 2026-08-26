"""ShortSqueezeEngine — Short Squeeze / 山寨币轧空 专项 Setup（V1.4 §十三–§二十二）。

目的：识别「空头拥挤 → 轧空蓄势 → 轧空触发 → 加速逼空 → 逼空尾声 → 退出」完整生命周期。

生命周期（§十四）：
    SHORT_CROWDING → SQUEEZE_BUILDUP → SQUEEZE_TRIGGER
        → SQUEEZE_ACCELERATION → SQUEEZE_EXHAUSTION → EXIT

关键规则（§四十二 Short Squeeze 组测试）：
- 仅 Funding 负 → 不触发（§十五：禁止「Funding 负 = 庄家控盘」，只能翻译为空头拥挤增强）。
- Funding 极负 + OI↑ + 价格强势下跌 → 不应误判轧空（§十八：buildup 需要「下跌效率衰减」，
  即卖压大但价格不再有效下跌 / CVD 降但价格横盘抬高）。
- Funding 极负 + OI↑ + 下跌效率衰减 → buildup。
- 5m 收盘突破 + Taker/CVD 确认 → trigger（§十九）。
- 价格↑ + OI↓ → squeeze acceleration 证据（§二十：空头回补型，区别于新增多头 OI↑）。
- Funding 回归中性 + OI↓ + 效率衰减 → exhaustion（§二十二）。

仅输出 setup/证据/状态，绝不翻译成「庄家一定做多」（§十五/§二十四）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SqueezePhase(str, Enum):
    """Short Squeeze 生命周期阶段（§十四）。"""

    NONE = "NONE"
    SHORT_CROWDING = "SHORT_CROWDING"           # 空头拥挤
    SQUEEZE_BUILDUP = "SQUEEZE_BUILDUP"         # 轧空蓄势
    SQUEEZE_TRIGGER = "SQUEEZE_TRIGGER"          # 轧空触发
    SQUEEZE_ACCELERATION = "SQUEEZE_ACCELERATION"  # 加速逼空
    SQUEEZE_EXHAUSTION = "SQUEEZE_EXHAUSTION"    # 逼空尾声
    EXIT = "EXIT"                               # 结束


class SqueezeType(str, Enum):
    """上涨类型分类（§二十）：新增多头 vs 空头回补（轧空）。"""

    NONE = "NONE"
    NEW_LONG = "NEW_LONG"          # Price↑ OI↑ Delta↑ CVD↑
    SHORT_SQUEEZE = "SHORT_SQUEEZE"  # Price↑↑ Delta↑↑ CVD↑ OI↓


@dataclass
class ShortSqueezeResult:
    """Short Squeeze 引擎输出。"""

    symbol: str
    phase: SqueezePhase
    squeeze_type: SqueezeType
    short_crowding_score: float       # 0~100 空头拥挤度（§十五）
    squeeze_strength: float           # 0~100 逼空强度（§二十一）
    positioning_divergence_score: float | None  # 0~100 普通户 vs 大户分歧（§二十四）
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "squeeze_type": self.squeeze_type.value,
            "short_crowding_score": round(self.short_crowding_score, 1),
            "squeeze_strength": round(self.squeeze_strength, 1),
            "positioning_divergence_score": (
                round(self.positioning_divergence_score, 1)
                if self.positioning_divergence_score is not None else None
            ),
            "label": self.label,
            "factors": dict(self.factors),
        }


@dataclass
class _SqueezeState:
    """单 symbol 轧空生命周期状态。"""

    symbol: str
    phase: SqueezePhase = SqueezePhase.NONE
    entered_at: int = 0
    last_update_ms: int = 0


# ── 阈值（命名常量；§四十 本轮不扩张 ML，使用可解释规则） ──────────────
FUNDING_PCT_EXTREME = 5.0          # funding_percentile <= 5 → 极负（最负的 5%）
FUNDING_ZSCORE_EXTREME = -2.0      # funding_zscore <= -2 → 极负
OI_EXPAND_MIN = 0.01               # OI 5m +1% 视为扩张
OI_CONTRACT_MIN = 0.005            # OI 5m -0.5% 视为收缩
PRICE_DOWN_STRONG = -0.01          # 5m 跌幅 < -1% → 强势下跌
TAKER_BUY_STRONG = 1.3            # taker B/S > 1.3 → 主动买增强
TAKER_BUY_SURGE = 1.6            # taker B/S > 1.6 → 主动买激增
CVD_STRONG_Z = 0.5               # cvd_slope_z > 0.5 → CVD 转强
EFFICIENCY_STRONG = 0.4          # price_efficiency > 0.4 → 推动有效
CROWD_SCORE_ENTER = 60.0         # short_crowding_score 入门空头拥挤


class ShortSqueezeEngine:
    """Short Squeeze 生命周期引擎（§十三）。每个 symbol 独立状态机。"""

    def __init__(self) -> None:
        self._states: dict[str, _SqueezeState] = {}

    def get_state(self, symbol: str) -> _SqueezeState:
        if symbol not in self._states:
            self._states[symbol] = _SqueezeState(symbol=symbol)
        return self._states[symbol]

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._states.clear()
        else:
            self._states.pop(symbol, None)

    def update(
        self,
        symbol: str,
        now_ms: int,
        fv: dict[str, Any] | None = None,
        breakout: dict[str, Any] | None = None,
    ) -> ShortSqueezeResult:
        """推进一个 symbol 的轧空生命周期。

        Args:
            fv: feature dict（funding/funding_zscore/funding_percentile/premium/
                global_account_ls_ratio/top_trader_account_ls_ratio/
                top_trader_position_ls_ratio/oi_change_pct_5m/oi_zscore/
                signed_delta/delta_ratio/taker_buy_sell_ratio/cvd_slope_z/
                price_efficiency/price_return_5m/acceptance/retrace_ratio/volume_z）。
            breakout: 突破生命周期 dict（breakout_confirmed/breakout_direction/
                close_back_inside/breakout_hold）。
        """
        fv = fv or {}
        bo = breakout or {}
        st = self.get_state(symbol)
        st.last_update_ms = now_ms

        # ── 基础信号 ──
        funding = _f(fv, "funding")
        funding_z = _f(fv, "funding_zscore")
        funding_pct = _f(fv, "funding_percentile")
        premium = _f(fv, "premium")
        global_ls = _f(fv, "global_account_ls_ratio")
        top_acc_ls = _f(fv, "top_trader_account_ls_ratio")
        top_pos_ls = _f(fv, "top_trader_position_ls_ratio")
        oi_pct_5m = _f(fv, "oi_change_pct_5m")
        signed_delta = _f(fv, "signed_delta") or _f(fv, "taker_delta")
        delta_ratio = _f(fv, "delta_ratio")
        taker_bs = _f(fv, "taker_buy_sell_ratio")
        cvd_slope_z = _f(fv, "cvd_slope_z")
        price_eff = _f(fv, "price_efficiency")
        price_ret_5m = _f(fv, "price_return_5m")
        acceptance = _f(fv, "acceptance")
        retrace = _f(fv, "retrace_ratio")
        volume_z = _f(fv, "volume_z")

        # ── 空头拥挤度（§十五）──
        crowd = self._short_crowding_score(
            funding, funding_z, funding_pct, premium,
            global_ls, top_acc_ls, top_pos_ls, oi_pct_5m,
        )
        # ── 普通户 vs 大户分歧（§二十四）──
        divergence = self._positioning_divergence(global_ls, top_acc_ls, top_pos_ls)
        # ── 逼空强度（§二十一）──
        strength = self._squeeze_strength(
            price_ret_5m, oi_pct_5m, taker_bs, cvd_slope_z, price_eff,
        )
        # ── 分类（§二十）──
        sqz_type = self._classify(price_ret_5m, oi_pct_5m, signed_delta, cvd_slope_z)

        # ── 关键布尔 ──
        funding_extreme = (
            (funding_pct is not None and funding_pct <= FUNDING_PCT_EXTREME)
            or (funding_z is not None and funding_z <= FUNDING_ZSCORE_EXTREME)
        )
        oi_expanding = oi_pct_5m is not None and oi_pct_5m > OI_EXPAND_MIN
        oi_contracting = oi_pct_5m is not None and oi_pct_5m < -OI_CONTRACT_MIN
        price_down_strong = price_ret_5m is not None and price_ret_5m < PRICE_DOWN_STRONG
        # §十八 下跌效率衰减：卖压大（delta 负 / CVD 降）但价格不再有效下跌
        sell_pressure = (
            (signed_delta is not None and signed_delta < 0)
            or (cvd_slope_z is not None and cvd_slope_z < 0)
        )
        downside_efficiency_collapse = (
            sell_pressure and not price_down_strong
        )
        absorption = (acceptance is not None and acceptance > 0.5) or (
            retrace is not None and retrace < 0.3
        )
        taker_buy_strong = taker_bs is not None and taker_bs > TAKER_BUY_STRONG
        taker_buy_surge = taker_bs is not None and taker_bs > TAKER_BUY_SURGE
        cvd_strong = cvd_slope_z is not None and cvd_slope_z > CVD_STRONG_Z
        eff_strong = price_eff is not None and price_eff > EFFICIENCY_STRONG
        breakout_up = bool(bo.get("breakout_confirmed")) and bo.get("breakout_direction") == "up"
        price_up = price_ret_5m is not None and price_ret_5m > 0
        funding_normalizing = (
            (funding_pct is not None and funding_pct > 25.0)
            or (funding is not None and funding > -0.0001)
        )
        taker_buy_fading = taker_bs is not None and taker_bs < 1.1
        cvd_not_new_high = cvd_slope_z is not None and cvd_slope_z < 0.2
        eff_dropping = price_eff is not None and price_eff < 0.2

        # ── 相位转移（§十四）──
        prev = st.phase
        new_phase = prev

        if prev in (SqueezePhase.NONE, SqueezePhase.EXIT):
            # 进入空头拥挤：需 funding 极负 + OI 扩张 + 拥挤分达标（禁止仅 funding 负触发）
            if funding_extreme and oi_expanding and crowd >= CROWD_SCORE_ENTER:
                new_phase = SqueezePhase.SHORT_CROWDING

        if prev == SqueezePhase.SHORT_CROWDING:
            # buildup：下跌效率衰减 + 承接增强（§十八）；若价格仍强势下跌则不进 buildup
            if downside_efficiency_collapse and (absorption or oi_expanding) and not price_down_strong:
                new_phase = SqueezePhase.SQUEEZE_BUILDUP
            elif not (funding_extreme or oi_expanding) and crowd < CROWD_SCORE_ENTER * 0.5:
                new_phase = SqueezePhase.NONE  # 拥挤消散

        if prev == SqueezePhase.SQUEEZE_BUILDUP:
            # trigger：5m 收盘突破 + 主动买增强 + CVD 转强 + 推动有效（§十九）
            if breakout_up and taker_buy_strong and cvd_strong and eff_strong:
                new_phase = SqueezePhase.SQUEEZE_TRIGGER
            elif not funding_extreme and crowd < CROWD_SCORE_ENTER * 0.5:
                new_phase = SqueezePhase.NONE

        if prev == SqueezePhase.SQUEEZE_TRIGGER:
            # acceleration：价格加速 + OI 收缩（空头回补）+ 主动买激增 + CVD 加速（§二十一）
            if price_up and oi_contracting and (taker_buy_surge or cvd_strong):
                new_phase = SqueezePhase.SQUEEZE_ACCELERATION
            # 直接进入尾声（突破失败 / 资金不继）
            elif taker_buy_fading and not price_up:
                new_phase = SqueezePhase.SQUEEZE_EXHAUSTION

        if prev == SqueezePhase.SQUEEZE_ACCELERATION:
            # exhaustion：OI 快降 + funding 回归 + 主动买衰减 + CVD 不创新高 + 效率降（§二十二）
            if (oi_contracting and funding_normalizing
                    and (taker_buy_fading or cvd_not_new_high) and (eff_dropping or not price_up)):
                new_phase = SqueezePhase.SQUEEZE_EXHAUSTION

        if prev == SqueezePhase.SQUEEZE_EXHAUSTION:
            # 退出：拥挤/强度双双消退
            if crowd < 20.0 and strength < 20.0:
                new_phase = SqueezePhase.EXIT
            # 重新加速
            elif price_up and oi_contracting and taker_buy_surge:
                new_phase = SqueezePhase.SQUEEZE_ACCELERATION

        if new_phase != prev:
            st.phase = new_phase
            st.entered_at = now_ms

        factors = {
            "funding": funding, "funding_zscore": funding_z, "funding_percentile": funding_pct,
            "premium": premium,
            "global_account_ls_ratio": global_ls,
            "top_trader_account_ls_ratio": top_acc_ls,
            "top_trader_position_ls_ratio": top_pos_ls,
            "oi_change_pct_5m": oi_pct_5m,
            "signed_delta": signed_delta, "delta_ratio": delta_ratio,
            "taker_buy_sell_ratio": taker_bs, "cvd_slope_z": cvd_slope_z,
            "price_efficiency": price_eff, "price_return_5m": price_ret_5m,
            "breakout_up": breakout_up, "oi_expanding": oi_expanding,
            "oi_contracting": oi_contracting, "downside_efficiency_collapse": downside_efficiency_collapse,
            "funding_extreme": funding_extreme, "price_down_strong": price_down_strong,
        }
        return ShortSqueezeResult(
            symbol=symbol, phase=st.phase, squeeze_type=sqz_type,
            short_crowding_score=crowd, squeeze_strength=strength,
            positioning_divergence_score=divergence,
            label=self._label(st.phase), factors=factors,
        )

    # ── 评分 ──────────────────────────────────────────────────────────

    def _short_crowding_score(
        self, funding, funding_z, funding_pct, premium,
        global_ls, top_acc_ls, top_pos_ls, oi_pct_5m,
    ) -> float:
        """§十五：空头拥挤度 0~100。

        组成：Funding 极值 + Premium/Basis + 普通户偏空（global L/S 低）+
        OI 扩张。禁止仅凭 Funding 负给高分（需多信号共振）。
        """
        score = 0.0
        # Funding 极值（zscore 或 percentile 任一极负）
        if funding_pct is not None:
            score += max(0.0, 35.0 * (1.0 - funding_pct / 100.0))   # pct 越低分越高
        elif funding_z is not None:
            score += min(35.0, max(0.0, -funding_z) * 12.0)
        # Premium / Basis 偏空（负 premium = 合约低于现货）
        if premium is not None and premium < 0:
            score += min(15.0, abs(premium) * 1000.0)
        # 普通户偏空（global L/S < 1）
        if global_ls is not None and global_ls < 1.0:
            score += min(25.0, (1.0 - global_ls) * 50.0)
        # OI 扩张（空头加仓）
        if oi_pct_5m is not None and oi_pct_5m > 0:
            score += min(25.0, oi_pct_5m * 1000.0)
        return min(100.0, round(score, 1))

    def _positioning_divergence(self, global_ls, top_acc_ls, top_pos_ls) -> float | None:
        """§二十四：普通户 vs 大户分歧度 0~100。

        普通户明显偏空（global L/S 低）但大户更偏多（top trader position L/S 高）
        → 分歧明显。仅作 Evidence，禁止翻译成「庄家一定做多」。
        """
        if global_ls is None or top_pos_ls is None:
            return None
        # 普通户偏空程度 + 大户偏多程度
        retail_short = max(0.0, 1.0 - global_ls)
        whale_long = max(0.0, top_pos_ls - 1.0)
        div = (retail_short + whale_long) * 50.0
        return min(100.0, round(div, 1))

    def _squeeze_strength(self, price_ret_5m, oi_pct_5m, taker_bs, cvd_slope_z, price_eff) -> float:
        """§二十一：逼空强度 0~100。

        价格加速 + OI 收缩（空头回补）+ 主动买激增 + CVD 加速 + 推动有效。
        """
        score = 0.0
        if price_ret_5m is not None and price_ret_5m > 0:
            score += min(35.0, price_ret_5m * 1000.0)
        if oi_pct_5m is not None and oi_pct_5m < 0:
            score += min(25.0, abs(oi_pct_5m) * 1000.0)   # OI 收缩加分
        if taker_bs is not None and taker_bs > 1.0:
            score += min(20.0, (taker_bs - 1.0) * 30.0)
        if cvd_slope_z is not None and cvd_slope_z > 0:
            score += min(10.0, cvd_slope_z * 10.0)
        if price_eff is not None and price_eff > 0:
            score += min(10.0, price_eff * 20.0)
        return min(100.0, round(score, 1))

    def _classify(self, price_ret_5m, oi_pct_5m, signed_delta, cvd_slope_z) -> SqueezeType:
        """§二十：区分新增多头上涨 vs 空头回补上涨。

        新增多头：Price↑ OI↑ Delta↑ CVD↑
        空头回补（轧空）：Price↑↑ Delta↑↑ CVD↑ OI↓
        """
        price_up = price_ret_5m is not None and price_ret_5m > 0
        oi_up = oi_pct_5m is not None and oi_pct_5m > 0
        oi_down = oi_pct_5m is not None and oi_pct_5m < 0
        delta_up = signed_delta is not None and signed_delta > 0
        cvd_up = cvd_slope_z is not None and cvd_slope_z > 0
        if price_up and oi_down and (delta_up or cvd_up):
            return SqueezeType.SHORT_SQUEEZE
        if price_up and oi_up and (delta_up or cvd_up):
            return SqueezeType.NEW_LONG
        return SqueezeType.NONE

    def _label(self, phase: SqueezePhase) -> str:
        return {
            SqueezePhase.NONE: "无轧空迹象",
            SqueezePhase.SHORT_CROWDING: "空头拥挤",
            SqueezePhase.SQUEEZE_BUILDUP: "轧空蓄势",
            SqueezePhase.SQUEEZE_TRIGGER: "轧空触发",
            SqueezePhase.SQUEEZE_ACCELERATION: "加速逼空",
            SqueezePhase.SQUEEZE_EXHAUSTION: "逼空尾声",
            SqueezePhase.EXIT: "轧空结束",
        }.get(phase, "无轧空迹象")


def _f(d: dict[str, Any], key: str) -> float | None:
    """从 feature dict 取 float（兼容 FeatureValue 与裸值）。"""
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("value")
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None   # NaN -> None
