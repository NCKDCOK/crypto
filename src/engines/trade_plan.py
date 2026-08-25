"""Trade Plan Engine — 分析计划（V1.2 §25 + V1.3 §18/§19）。

它只是分析计划，不是自动交易（AI_RULES 硬规则1）。

§25.2 Entry 生成原则：必须来自结构（Breakout Level / Retest Zone / Support/Resistance /
POC/VAH/VAL / VWAP / Swing / Failed Zone / ATR），不能由 AI 自由生成。
§25.3 Invalidation：先确定什么位置被破坏后 Setup 不成立 → 1R。
§25.4 TP：候选 2R / 3.2R / structure target，检查前方真实阻力。RR 不足输出「不建议追入」。
§25.5 Trade Plan 冻结：START_CONFIRMED 或正式 Setup Push 时冻结 snapshot，禁止随价格漂移。

V1.3 §18 状态限制（严格）：
  - 正式 Trade Plan：START_CONFIRMED / CONTINUATION
  - 只能生成候选预案：SUSPECTED_START（以及子阶段标签 ACCUMULATION / RETEST_PENDING，
    方案A：子阶段/等待条件，非机器状态）→ UI 必须写「候选预案，尚未确认」
  - 不生成正式计划：其余状态（SLEEPING / ANOMALY / COOLDOWN / EXHAUSTION /
    WITHDRAWAL / REJECTED）

V1.3 §19 版本冻结：
  - 每次正式 Setup 创建：trade_plan_id / version / created_at / status
  - 禁止每秒随价格重算并覆盖旧计划
  - 后续变化只能：V1 → EXPIRED，V2 → NEW PLAN
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from typing import Any

from src.domain.enums import State

# ── §18 合法状态分级 ──
# 正式 Trade Plan
FORMAL_STATES: frozenset = frozenset({State.START_CONFIRMED, State.CONTINUATION})
# 候选子阶段标签（方案A：子阶段/等待条件，仅监督列展示，非机器状态）
CANDIDATE_SUB_STAGES: frozenset = frozenset({"ACCUMULATION", "RETEST_PENDING"})

# ── §19 status 取值 ──
STATUS_NOT_LEGAL = "NOT_LEGAL"      # 不生成正式计划（状态不在合法范围 / 数据不足）
STATUS_CANDIDATE = "CANDIDATE"      # 候选预案，尚未确认
STATUS_ACTIVE = "ACTIVE"            # 正式 Trade Plan（已冻结 / 可冻结）
STATUS_EXPIRED = "EXPIRED"          # V-n 已过期 → V-n+1 NEW PLAN


def plan_gate(state: State | str | None, sub_stage: str | None = None) -> str:
    """§18 状态限制 → 返回 "formal" / "candidate" / "none"。

    - state 为 None（旧调用未传状态）时按正式处理，保持向后兼容。
    - 子阶段标签（ACCUMULATION / RETEST_PENDING）按候选预案处理。
    """
    if state is None:
        return "formal"
    if state in FORMAL_STATES:
        return "formal"
    if state == State.SUSPECTED_START or (
        sub_stage and str(sub_stage).upper() in CANDIDATE_SUB_STAGES
    ):
        return "candidate"
    return "none"


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
    # V1.3 §18/§19
    status: str = STATUS_NOT_LEGAL
    trade_plan_id: str | None = None
    version: int = 0
    created_at: int | None = None
    setup_snapshot_id: str | None = None

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
            "status": self.status,
            "trade_plan_id": self.trade_plan_id,
            "version": self.version,
            "created_at": self.created_at,
            "setup_snapshot_id": self.setup_snapshot_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradePlan":
        """从 to_dict() 结果还原（§19 冻结快照原样恢复）。"""
        return cls(
            current_price=data.get("current_price"),
            reference_entry_low=data.get("reference_entry_low"),
            reference_entry_high=data.get("reference_entry_high"),
            invalidation_price=data.get("invalidation_price"),
            tp1=data.get("tp1"), tp2=data.get("tp2"), tp3=data.get("tp3"),
            rr_tp1=data.get("rr_tp1"), rr_tp2=data.get("rr_tp2"),
            rr_tp3=data.get("rr_tp3"),
            chase_status=data.get("chase_status", "no_plan"),
            plan_reason=data.get("plan_reason", ""),
            frozen=data.get("frozen", False),
            frozen_at_ms=data.get("frozen_at_ms"),
            expired=data.get("expired", False),
            status=data.get(
                "status",
                STATUS_ACTIVE if data.get("frozen") else STATUS_NOT_LEGAL,
            ),
            trade_plan_id=data.get("trade_plan_id"),
            version=data.get("version", 0),
            created_at=data.get("created_at"),
            setup_snapshot_id=data.get("setup_snapshot_id"),
        )


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
        # §19：每个 symbol 已冻结的最大版本（V1 → EXPIRED → V2 → ...）
        self._versions: dict[str, int] = {}

    def compute(
        self,
        current_price: float | None,
        direction: str | None,
        *,
        structure=None,
        volume_profile=None,
        atr: float | None = None,
        state: State | str | None = None,
        sub_stage: str | None = None,
    ) -> TradePlan:
        """生成交易计划（V1.3 §18 状态限制）。

        Entry 来自结构（support/resistance/breakout_level/retest_zone/poc/vwap）。
        state / sub_stage 传入后按 §18 分级：
        - 不合法状态 → status=NOT_LEGAL，不生成正式计划
        - 候选（SUSPECTED_START / ACCUMULATION / RETEST_PENDING）→ status=CANDIDATE
        - 正式（START_CONFIRMED / CONTINUATION）→ status=ACTIVE
        """
        if current_price is None or direction is None:
            return TradePlan(None, None, None, None, None, None, None, None, None, None,
                             "no_plan", "数据不足，无法生成计划", status=STATUS_NOT_LEGAL)

        gate = plan_gate(state, sub_stage)
        if gate == "none":
            state_text = state.value if isinstance(state, State) else str(state)
            return TradePlan(None, None, None, None, None, None, None, None, None, None,
                             "no_plan",
                             f"当前状态 {state_text} 不在合法生成范围，不生成交易计划（§18）",
                             status=STATUS_NOT_LEGAL)

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

        status = STATUS_ACTIVE if gate == "formal" else STATUS_CANDIDATE
        if status == STATUS_CANDIDATE:
            plan_reason = f"候选预案，尚未确认；{plan_reason}" if plan_reason else "候选预案，尚未确认"

        return TradePlan(
            current_price=current_price,
            reference_entry_low=entry_low,
            reference_entry_high=entry_high,
            invalidation_price=invalidation,
            tp1=tp1, tp2=tp2, tp3=tp3,
            rr_tp1=rr1, rr_tp2=rr2, rr_tp3=rr3,
            chase_status=chase_status,
            plan_reason=plan_reason,
            status=status,
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

    def freeze(self, plan: TradePlan, now_ms: int, *, symbol: str | None = None) -> TradePlan:
        """冻结 Trade Plan snapshot（§19：每次正式 Setup 创建，禁止覆盖旧计划）。

        只有 status=ACTIVE 的正式计划可冻结；已冻结计划原样返回。
        同一 symbol 每次新 Setup 版本 +1（V1 → EXPIRED → V2 → NEW PLAN）。
        """
        if plan.status != STATUS_ACTIVE or (plan.frozen and plan.trade_plan_id):
            return plan
        if not plan.trade_plan_id:
            plan.trade_plan_id = _uuid.uuid4().hex
        if symbol:
            plan.version = self._versions.get(symbol, 0) + 1
            self._versions[symbol] = plan.version
        else:
            plan.version = (plan.version or 0) + 1
        plan.created_at = now_ms
        plan.frozen = True
        plan.frozen_at_ms = now_ms
        return plan

    def expire(self, plan: TradePlan, now_ms: int) -> TradePlan:
        """§19：V1 → EXPIRED（旧 Setup 失效，等待 V2 NEW PLAN）。"""
        plan.status = STATUS_EXPIRED
        plan.expired = True
        return plan
