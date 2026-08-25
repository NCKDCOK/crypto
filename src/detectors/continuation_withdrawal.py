"""Continuation / Exhaustion / Withdrawal Detectors。

依据：ANALYSIS_MODEL.md §5.3/§5.4, STATE_MACHINE.md T6-T11
- Continuation: OI 持续、CVD 同向、回踩卖压减弱、效率健康
- Exhaustion: 价格创新高但 CVD/OI/效率不确认
- Withdrawal: OI 收缩 + delta/CVD 反转 + 主动卖出持续 + 价格失守

撤离不是"启动条件取反"，是独立模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from src.domain import Direction, Evidence, EvidenceFamily, FeatureSnapshot


@dataclass
class ContinuationResult:
    """Continuation 检测结果。"""

    is_continuing: bool
    is_weakening: bool
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class ExhaustionResult:
    """Exhaustion 检测结果。"""

    is_exhausted: bool
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class WithdrawalResult:
    """Withdrawal 检测结果。"""

    is_withdrawal: bool
    evidence: list[Evidence] = field(default_factory=list)
    reason: str | None = None


class ContinuationDetector:
    """Continuation Detector — 主力还在不在。

    V1.2 §21：持续启动必须来自真实资金证据，禁止「因为在 CONTINUATION 所以高分」。
    证据：OI persistence / CVD persistence / Delta persistence / repeated active flow /
          healthy retrace / second impulse / price efficiency / breakout hold。
    """

    def __init__(self, min_oi_maintain: float = 0.0,
                 min_evidence_count: int = 2) -> None:
        self.min_oi_maintain = min_oi_maintain
        self.min_evidence_count = min_evidence_count

    def detect(
        self,
        snap: FeatureSnapshot,
        direction: Direction | None = None,
    ) -> ContinuationResult:
        evidence: list[Evidence] = []
        passed_count = 0

        # OI 维持或扩张（OI persistence）
        oi = snap.features.get("oi_change_1m")
        if oi and oi.available and oi.value is not None:
            oi_maintained = oi.value >= self.min_oi_maintain
            evidence.append(Evidence(
                family=EvidenceFamily.POSITION,
                type="oi_persistence",
                value=oi.value,
                passed=oi_maintained,
            ))
            if oi_maintained:
                passed_count += 1

        # CVD 维持方向（CVD persistence）
        cvd_slope = snap.features.get("cvd_slope_z")
        if cvd_slope and cvd_slope.available and cvd_slope.value is not None:
            cvd_maintained = (
                (direction == Direction.LONG and cvd_slope.value > 0)
                or (direction == Direction.SHORT and cvd_slope.value < 0)
                or direction is None
            )
            evidence.append(Evidence(
                family=EvidenceFamily.FLOW,
                type="cvd_persistence",
                value=cvd_slope.value,
                passed=cvd_maintained,
            ))
            if cvd_maintained:
                passed_count += 1

        # Delta persistence（主动资金持续）
        delta = snap.features.get("taker_delta") or snap.features.get("signed_delta")
        if delta and delta.available and delta.value is not None:
            delta_maintained = (
                (direction == Direction.LONG and delta.value > 0)
                or (direction == Direction.SHORT and delta.value < 0)
                or direction is None
            )
            evidence.append(Evidence(
                family=EvidenceFamily.FLOW,
                type="delta_persistence",
                value=delta.value,
                passed=delta_maintained,
            ))
            if delta_maintained:
                passed_count += 1

        # Price efficiency 健康（healthy retrace + efficiency）
        eff = snap.features.get("directional_efficiency")
        if eff and eff.available and eff.value is not None:
            eff_healthy = eff.value > 0.2
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="efficiency_healthy",
                value=eff.value,
                passed=eff_healthy,
            ))
            if eff_healthy:
                passed_count += 1

        # Healthy retrace（回撤可控 = 回踩健康）
        retrace = snap.features.get("retrace_ratio")
        if retrace and retrace.available and retrace.value is not None:
            retrace_healthy = retrace.value < 0.5
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="healthy_retrace",
                value=retrace.value,
                passed=retrace_healthy,
            ))
            if retrace_healthy:
                passed_count += 1

        # V1.2 §21：必须达到 min_evidence_count 才算 continuing（真实证据化）
        is_continuing = passed_count >= self.min_evidence_count
        any_failed = any(not e.passed for e in evidence) if evidence else True

        return ContinuationResult(
            is_continuing=is_continuing,
            is_weakening=any_failed,
            evidence=evidence,
        )


class ExhaustionDetector:
    """Exhaustion Detector — 推动效率下降、背离增加。"""

    def __init__(self, min_divergence_count: int = 2) -> None:
        self.min_divergence_count = min_divergence_count

    def detect(
        self,
        snap: FeatureSnapshot,
        direction: Direction | None = None,
    ) -> ExhaustionResult:
        evidence: list[Evidence] = []
        divergence_count = 0

        # CVD 不创新高/转弱
        cvd_slope = snap.features.get("cvd_slope_z")
        if cvd_slope and cvd_slope.available and cvd_slope.value is not None:
            cvd_weakening = (
                (direction == Direction.LONG and cvd_slope.value < 0)
                or (direction == Direction.SHORT and cvd_slope.value > 0)
            )
            evidence.append(Evidence(
                family=EvidenceFamily.FLOW,
                type="cvd_divergence",
                value=cvd_slope.value,
                passed=cvd_weakening,
            ))
            if cvd_weakening:
                divergence_count += 1

        # OI 走平或收缩
        oi = snap.features.get("oi_change_1m")
        if oi and oi.available and oi.value is not None:
            oi_contracting = oi.value < 0
            evidence.append(Evidence(
                family=EvidenceFamily.POSITION,
                type="oi_contracting",
                value=oi.value,
                passed=oi_contracting,
            ))
            if oi_contracting:
                divergence_count += 1

        # FlowImpact 持续下降
        flow_impact = snap.features.get("flow_impact")
        if flow_impact and flow_impact.available and flow_impact.value is not None:
            fi_low = abs(flow_impact.value) < 0.001
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="flow_impact_declining",
                value=flow_impact.value,
                passed=fi_low,
            ))
            if fi_low:
                divergence_count += 1

        is_exhausted = divergence_count >= self.min_divergence_count

        return ExhaustionResult(
            is_exhausted=is_exhausted,
            evidence=evidence,
        )


class WithdrawalDetector:
    """Withdrawal Detector — 资金撤离（独立模型，不是启动条件取反）。

    撤离条件：
    - OI 收缩
    - delta/CVD 反转
    - 主动卖出持续增强
    - 价格结构失守
    """

    def __init__(self, min_evidence_count: int = 3) -> None:
        self.min_evidence_count = min_evidence_count

    def detect(
        self,
        snap: FeatureSnapshot,
        direction: Direction | None = None,
    ) -> WithdrawalResult:
        evidence: list[Evidence] = []
        confirm_count = 0

        # OI 收缩
        oi = snap.features.get("oi_change_1m")
        if oi and oi.available and oi.value is not None:
            oi_contracting = oi.value < 0
            evidence.append(Evidence(
                family=EvidenceFamily.POSITION,
                type="oi_withdrawal",
                value=oi.value,
                passed=oi_contracting,
            ))
            if oi_contracting:
                confirm_count += 1

        # delta/CVD 反转
        delta = snap.features.get("taker_delta")
        if delta and delta.available and delta.value is not None:
            delta_reversed = (
                (direction == Direction.LONG and delta.value < 0)
                or (direction == Direction.SHORT and delta.value > 0)
            )
            evidence.append(Evidence(
                family=EvidenceFamily.FLOW,
                type="delta_reversal",
                value=delta.value,
                passed=delta_reversed,
            ))
            if delta_reversed:
                confirm_count += 1

        # 效率失守
        eff = snap.features.get("directional_efficiency")
        if eff and eff.available and eff.value is not None:
            eff_broken = eff.value < 0.1
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="efficiency_broken",
                value=eff.value,
                passed=eff_broken,
            ))
            if eff_broken:
                confirm_count += 1

        # 回吐大
        retrace = snap.features.get("retrace_ratio")
        if retrace and retrace.available and retrace.value is not None:
            retrace_large = retrace.value > 0.5
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="retrace_large",
                value=retrace.value,
                passed=retrace_large,
            ))
            if retrace_large:
                confirm_count += 1

        is_withdrawal = confirm_count >= self.min_evidence_count

        return WithdrawalResult(
            is_withdrawal=is_withdrawal,
            evidence=evidence,
            reason="withdrawal_confirmed" if is_withdrawal else None,
        )
