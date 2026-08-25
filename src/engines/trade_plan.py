"""Trade Plan Engine — 分析计划（V1.2 §25）。

它只是分析计划，不是自动交易（AI_RULES 硬规则1）。

§25.2 Entry 生成原则：必须来自结构（Breakout Level / Retest Zone / Support/Resistance /
POC/VAH/VAL / VWAP / Swing / Failed Zone / ATR），不能由 AI 自由生成。
§25.3 Invalidation：先确定什么位置被破坏后 Setup 不成立 → 1R。
§25.4 TP：候选 2R / 3.2R / structure target，检查前方真实阻力。RR 不足输出「不建议追入」。
§25.5 Trade Plan 冻结：START_CONFIRMED 或正式 Setup Push 时冻结 snapshot，禁止随价格漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TradePlan:
    """交易分析计划（只读分析，非自动交易）。"""
    current_price: float | None
    reference_entry_low: float | None
    reference_entry_high: float | None
    invalidation_price: float | None
    tp1: float | None
    tp2: float | None
    tp3: float | None
    rr_tp1: float | None
    rr_tp2: float | None
    rr_tp3: float | None
    chase_status: str  # ok / chase_too_far / insufficient_rr / no_plan
    plan_reason: str
    frozen: bool = False
    frozen_at_ms: int | None = None
    expired: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_price": self.current_price,
            "reference_entry_low": self.reference_entry_low,
            "reference_entry_high": self.reference_entry_high,
            "invalidation_price": self.invalidation_price,
            "tp1": self.tp1, "tp2": self.tp2, "tp3": self.tp3,
            "rr_tp1": self.rr_tp1, "rr_tp2": self.rr_tp2, "rr_tp3": self.rr_tp3,
            "chase_status": self.chase_status,
            "plan_reason": self.plan_reason,
            "frozen": self.frozen,
            "frozen_at_ms": self.frozen_at_ms,
            "expired": self.expired,
        }


class TradePlanEngine:
    """交易计划引擎。"""

    def __init__(
        self,
        min_rr: float = 1.5,
        chase_too_far_pct: float = 0.05,
        tp1_r: float = 2.0,
        tp2_r: float = 3.2,
        tp3_structure: bool = True,
    ) -> None:
        self.min_rr = min_rr
        self.chase_too_far_pct = chase_too_far_pct
        self.tp1_r = tp1_r
        self.tp2_r = tp2_r
        self.tp3_structure = tp3_structure

    def compute(
        self,
        current_price: float | None,
        direction: str | None,
        *,
        structure=None,
        volume_profile=None,
        atr: float | None = None,
    ) -> TradePlan:
        """生成交易计划。

        Entry 来自结构（support/resistance/breakout_level/retest_zone/poc/vwap）。
        """
        if current_price is None or direction is None:
            return TradePlan(None, None, None, None, None, None, None, None, None, None,
                             "no_plan", "数据不足，无法生成计划")

        sign = 1.0 if direction == "LONG" else -1.0
        # ── Entry Zone（来自结构）──
        entry_low = None
        entry_high = None

        # 优先用 retest_zone / support(LONG) / resistance(SHORT)
        if structure:
            if sign > 0:  # LONG
                if structure.retest_zone_low and structure.retest_zone_high:
                    entry_low = structure.retest_zone_low
                    entry_high = structure.retest_zone_high
                elif structure.support:
                    entry_low = structure.support
                    entry_high = current_price
            else:  # SHORT
                if structure.retest_zone_low and structure.retest_zone_high:
                    entry_low = structure.retest_zone_low
                    entry_high = structure.retest_zone_high
                elif structure.resistance:
                    entry_low = current_price
                    entry_high = structure.resistance

        # fallback: POC / VWAP
        if entry_low is None and volume_profile and volume_profile.poc:
            entry_low = volume_profile.poc
            entry_high = current_price
        if entry_low is None and structure and structure.vwap:
            entry_low = structure.vwap
            entry_high = current_price

        # ── Invalidation（结构失效位）──
        invalidation = None
        if structure:
            if sign > 0 and structure.support:
                invalidation = structure.support
                if atr:
                    invalidation = structure.support - atr * 0.5  # 略低于支撑
            elif sign < 0 and structure.resistance:
                invalidation = structure.resistance
                if atr:
                    invalidation = structure.resistance + atr * 0.5
        if invalidation is None and entry_low:
            invalidation = entry_low - (current_price - entry_low) * 0.5 if sign > 0 else entry_high + (entry_high - current_price) * 0.5

        # ── Entry 参考价（Entry Zone 中点）──
        entry_ref = None
        if entry_low is not None and entry_high is not None:
            entry_ref = (entry_low + entry_high) / 2.0
        elif entry_low is not None:
            entry_ref = entry_low

        # ── 1R = |entry - invalidation| ──
        one_r = None
        if entry_ref is not None and invalidation is not None:
            one_r = abs(entry_ref - invalidation)

        # ── TP（2R / 3.2R / structure target）──
        tp1 = tp2 = tp3 = None
        rr1 = rr2 = rr3 = None
        if one_r and one_r > 0 and entry_ref is not None:
            tp1 = entry_ref + sign * one_r * self.tp1_r
            tp2 = entry_ref + sign * one_r * self.tp2_r
            # TP3 = structure target（前方阻力/支撑）
            if self.tp3_structure and structure:
                if sign > 0 and structure.resistance:
                    tp3 = structure.resistance
                elif sign < 0 and structure.support:
                    tp3 = structure.support
                else:
                    tp3 = entry_ref + sign * one_r * 4.0
            else:
                tp3 = entry_ref + sign * one_r * 4.0
            rr1 = self.tp1_r
            rr2 = self.tp2_r
            if tp3 and entry_ref:
                rr3 = abs(tp3 - entry_ref) / one_r

        # ── Chase status ──
        dist_from_entry = None
        if entry_ref is not None and current_price:
            dist_from_entry = abs(current_price - entry_ref) / current_price

        chase_status = "ok"
        plan_reason = ""
        if dist_from_entry is not None and dist_from_entry > self.chase_too_far_pct:
            chase_status = "chase_too_far"
            plan_reason = f"当前已偏离 Entry 较远（{dist_from_entry:.1%}），不建议直接追价"
        elif rr1 is not None and rr1 < self.min_rr:
            chase_status = "insufficient_rr"
            plan_reason = f"风险收益不足（R:R={rr1:.1f}），不建议追入"
        elif tp3 is not None and rr3 is not None and rr3 < 1.1:
            chase_status = "insufficient_rr"
            plan_reason = f"结构目标 R:R 不足（{rr3:.1f}），不建议追入"
        else:
            plan_reason = self._build_reason(direction, entry_ref, invalidation, tp1, tp2, rr1)

        return TradePlan(
            current_price=current_price,
            reference_entry_low=entry_low,
            reference_entry_high=entry_high,
            invalidation_price=invalidation,
            tp1=tp1, tp2=tp2, tp3=tp3,
            rr_tp1=rr1, rr_tp2=rr2, rr_tp3=rr3,
            chase_status=chase_status,
            plan_reason=plan_reason,
        )

    def _build_reason(self, direction, entry, invalidation, tp1, tp2, rr1):
        parts = []
        if direction == "LONG":
            parts.append("多头计划")
        else:
            parts.append("空头计划")
        if entry:
            parts.append(f"参考关注区 {entry:.4f}")
        if invalidation:
            parts.append(f"结构失效位 {invalidation:.4f}")
        if tp1 and tp2:
            parts.append(f"TP1 {tp1:.4f} / TP2 {tp2:.4f}")
        if rr1:
            parts.append(f"R:R {rr1:.1f}")
        return "，".join(parts)

    def freeze(self, plan: TradePlan, now_ms: int) -> TradePlan:
        """冻结 Trade Plan snapshot（§25.5，START_CONFIRMED 时调用）。"""
        plan.frozen = True
        plan.frozen_at_ms = now_ms
        return plan
