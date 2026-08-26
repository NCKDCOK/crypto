"""RecommendationGate — 正式推荐门禁（V1.4 §三）。

禁止再使用「当前 score > 70 就立刻成为首页推荐」（§三）。

标准确认（§3.1）：
    state ∈ {START_CONFIRMED, CONTINUATION}
    Opportunity >= 70
    Signal Confirmation >= 75
    Data Confidence >= 85
    5m closed confirmation（§四：发布决策绑定新 5m 收盘窗口；突破类 Setup 额外要求
        breakout_confirmed=true）
    Hard Veto = false
    Trade Plan = ACTIVE（且 chase_status=ok，Entry Zone 有效）
    RR >= minimum_rr
    Pump Risk < threshold
    核心证据 >= 3/3
    辅助证据 >= 3/5

强确认（§3.2）——标准确认通过后，进一步满足 6 项 → confirmation_level=STRONG：
    breakout_hold / retest_confirmed / second_impulse_confirmed /
    15m direction aligned / 1h not strongly opposite / spot_perp_agreement

阈值全部来自 configs/recommendation.yaml（RecommendationConfig），禁止 magic number。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.config import RecommendationConfig

# 正式状态范围（与 trade_plan.FORMAL_STATES 对齐）
FORMAL_STATES: frozenset = frozenset({"START_CONFIRMED", "CONTINUATION"})

# 突破类 Setup：发布必须要求 5m 收盘突破确认（breakout_confirmed=true）
BREAKOUT_SETUPS: frozenset = frozenset({
    "BREAKOUT_START",
    "RETEST_REIGNITION",
    "TREND_CONTINUATION",
    "SHORT_SQUEEZE",
})

# 非突破类 Setup：要求最新 5m K 线已收盘且数据可用（由调用方保证 five_min_closed）
NON_BREAKOUT_SETUPS: frozenset = frozenset({"ACCUMULATION", "OVERSOLD_REBOUND"})


@dataclass(frozen=True)
class GateContext:
    """Gate 判定所需全部运行时上下文（由 runtime 组装）。"""

    state: str
    setup_type: str
    opportunity_score: float | None
    signal_confirmation: float | None
    data_confidence: float | None
    trade_plan: dict[str, Any] | None
    pump_risk: float | None
    stale_flag: float | None
    direction: str | None = None      # LONG / SHORT（强确认多周期对齐用）
    hard_veto: bool = False
    five_min_closed: bool = False       # 本 tick 处于新 5m 收盘决策窗口（§四）
    breakout_confirmed: bool | None = None  # 突破类 Setup 的 5m 收盘站外确认
    # 证据投票（来自 SignalConfirmationBreakdown）
    core_passed: int = 0
    core_total: int = 0
    aux_passed: int = 0
    aux_total: int = 0
    # 强确认（§3.2）
    breakout_hold: bool | None = None
    retest_confirmed: bool | None = None
    second_impulse_confirmed: bool | None = None
    context_15m: float | None = None
    context_1h: float | None = None
    spot_perp_agreement: float | None = None


@dataclass(frozen=True)
class GateResult:
    """Gate 判定结果。"""

    passed: bool
    confirmation_level: str | None = None   # STANDARD / STRONG
    failed_checks: list[str] = field(default_factory=list)
    strong_missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "confirmation_level": self.confirmation_level,
            "failed_checks": list(self.failed_checks),
            "strong_missing": list(self.strong_missing),
        }


class RecommendationGate:
    """正式推荐门禁（§三）。"""

    def __init__(self, config: RecommendationConfig) -> None:
        self.cfg = config
        self.breakout_setups = frozenset(config.breakout_require_setups) or BREAKOUT_SETUPS

    # ── 标准确认（§3.1）──

    def evaluate(self, ctx: GateContext) -> GateResult:
        failed: list[str] = []

        # 1. 状态正式范围
        if ctx.state not in FORMAL_STATES:
            failed.append(f"state={ctx.state} 不在正式范围")
        # 2. 三门槛
        if ctx.opportunity_score is None or ctx.opportunity_score < self.cfg.min_opportunity:
            failed.append(f"opportunity={ctx.opportunity_score} < {self.cfg.min_opportunity}")
        if ctx.signal_confirmation is None or ctx.signal_confirmation < self.cfg.min_signal_confirmation:
            failed.append(
                f"signal_confirmation={ctx.signal_confirmation} < {self.cfg.min_signal_confirmation}")
        if ctx.data_confidence is None or ctx.data_confidence < self.cfg.min_data_confidence:
            failed.append(f"data_confidence={ctx.data_confidence} < {self.cfg.min_data_confidence}")
        # 3. 5m closed confirmation（§四 决策边界）
        if not ctx.five_min_closed:
            failed.append("不在 5m 收盘决策窗口（等待新 5m 收盘）")
        is_breakout_setup = ctx.setup_type in self.breakout_setups
        if is_breakout_setup and not ctx.breakout_confirmed:
            failed.append("突破类 Setup 但 5m 收盘未确认突破（breakout_confirmed=false）")
        # 4. Hard Veto
        if ctx.hard_veto:
            failed.append("存在 Hard Veto")
        # 5. Trade Plan
        plan = ctx.trade_plan or {}
        if plan.get("status") != "ACTIVE":
            failed.append(f"trade_plan.status={plan.get('status')} != ACTIVE")
        if plan.get("chase_status") not in (None, "ok"):
            failed.append(f"trade_plan.chase_status={plan.get('chase_status')}（不建议追入）")
        zone_low, zone_high = plan.get("reference_entry_low"), plan.get("reference_entry_high")
        if zone_low is None or zone_high is None or zone_low <= 0 or zone_low > zone_high:
            failed.append("Entry Zone 无效（reference_entry_low/high 缺失或非法）")
        # 6. RR >= minimum_rr
        rr1 = plan.get("rr_tp1")
        if rr1 is None or rr1 < self.cfg.minimum_rr:
            failed.append(f"RR={rr1} < minimum_rr={self.cfg.minimum_rr}")
        # 7. Pump Risk
        if ctx.pump_risk is not None and ctx.pump_risk >= self.cfg.max_pump_risk:
            failed.append(f"pump_risk={ctx.pump_risk} >= {self.cfg.max_pump_risk}")
        # 8. stale
        if ctx.stale_flag:
            failed.append("stale_flag 命中")
        # 9. 证据投票（核心 >= 3/3，辅助 >= 3/5）
        if ctx.core_total < self.cfg.core_min_total or ctx.core_passed < self.cfg.core_min_passed:
            failed.append(
                f"核心证据 {ctx.core_passed}/{ctx.core_total} < {self.cfg.core_min_passed}/{self.cfg.core_min_total}")
        if ctx.aux_total < self.cfg.aux_min_total or ctx.aux_passed < self.cfg.aux_min_passed:
            failed.append(
                f"辅助证据 {ctx.aux_passed}/{ctx.aux_total} < {self.cfg.aux_min_passed}/{self.cfg.aux_min_total}")

        if failed:
            return GateResult(passed=False, failed_checks=failed)

        # ── 强确认（§3.2）──
        level = "STANDARD"
        strong_missing: list[str] = []
        if not ctx.breakout_hold:
            strong_missing.append("breakout_hold")
        if not ctx.retest_confirmed:
            strong_missing.append("retest_confirmed")
        if not ctx.second_impulse_confirmed:
            strong_missing.append("second_impulse_confirmed")
        sign = 1.0 if ctx.direction == "LONG" else (-1.0 if ctx.direction == "SHORT" else 0.0)
        if ctx.context_15m is None or ctx.context_15m * sign <= 0:
            strong_missing.append("15m_direction_aligned")
        if ctx.context_1h is not None and ctx.context_1h * sign < -self.cfg.strong_1h_opposite_threshold:
            strong_missing.append("1h_not_strongly_opposite")
        if ctx.spot_perp_agreement is None or ctx.spot_perp_agreement < self.cfg.strong_spot_agreement_min:
            strong_missing.append("spot_perp_agreement")
        if not strong_missing:
            level = "STRONG"

        return GateResult(passed=True, confirmation_level=level,
                          strong_missing=strong_missing)