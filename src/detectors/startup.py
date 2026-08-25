"""Startup Detector — 建立"新增资金推动"证据链。

依据：ANALYSIS_MODEL.md §5.2, STATE_MACHINE.md T3/T5
证据链：候选方向 + 量异常 + 主动成交同向 + OI 同向扩张（或 squeeze 例外）
       + 价格有效位移 + 突破后未全部回吐
→ SUSPECTED_START / START_CONFIRMED

必须区分"新增多头启动"与"空头回补 squeeze"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from src.domain import ConfidenceState, Direction, Evidence, EvidenceFamily, FeatureSnapshot
from .anomaly import AnomalyResult


@dataclass
class StartupResult:
    """Startup 检测结果。"""

    suspected: bool  # SUSPECTED_START
    confirmed: bool  # START_CONFIRMED
    direction: Direction | None
    evidence: list[Evidence] = field(default_factory=list)
    is_squeeze_cover: bool = False  # 空头回补（非新增多头）
    reason: str | None = None


class StartupDetector:
    """Startup Detector。

    从 anomaly 候选中建立方向 + 新增资金 + 价格效果证据链。
    """

    def __init__(
        self,
        confirmation_hold_s: float = 15.0,
        oi_expansion_threshold: float = 0.0,  # OI 变化 > 0 视为扩张
        min_efficiency: float = 0.2,  # 最低 directional efficiency
        max_retrace: float = 0.8,  # 最大回吐比例
    ) -> None:
        self.confirmation_hold_s = confirmation_hold_s
        self.oi_expansion_threshold = oi_expansion_threshold
        self.min_efficiency = min_efficiency
        self.max_retrace = max_retrace

    def detect(
        self,
        snap: FeatureSnapshot,
        anomaly: AnomalyResult,
        confidence: ConfidenceState = ConfidenceState.CONFIDENT,
        hold_duration_s: float = 0.0,
    ) -> StartupResult:
        """检测启动。

        Args:
            snap: FeatureSnapshot
            anomaly: Anomaly 检测结果
            confidence: 当前 ConfidenceState
            hold_duration_s: 证据已持续时间（秒）

        Returns:
            StartupResult
        """
        if not anomaly.is_anomaly:
            return StartupResult(suspected=False, confirmed=False, direction=None)

        if confidence == ConfidenceState.UNKNOWN:
            return StartupResult(
                suspected=False, confirmed=False, direction=None,
                reason="confidence_unknown",
            )

        evidence: list[Evidence] = []
        direction = None
        is_squeeze_cover = False

        # 方向从 taker_delta / cvd_slope_z 推断
        delta = snap.features.get("taker_delta")
        if delta and delta.available and delta.value is not None:
            if delta.value > 0:
                direction = Direction.LONG
            elif delta.value < 0:
                direction = Direction.SHORT
            evidence.append(Evidence(
                family=EvidenceFamily.FLOW,
                type="taker_delta",
                value=delta.value,
                passed=delta.value != 0,
            ))

        # OI 同向扩张检查
        oi_change = snap.features.get("oi_change_1m")
        if oi_change and oi_change.available and oi_change.value is not None and direction:
            oi_expanding = oi_change.value > self.oi_expansion_threshold
            evidence.append(Evidence(
                family=EvidenceFamily.POSITION,
                type="oi_expansion" if oi_expanding else "oi_contraction_cover",
                value=oi_change.value,
                passed=oi_expanding,
            ))
            # squeeze 例外：direction=LONG 且 OI 收缩 → cover
            if direction == Direction.LONG and not oi_expanding:
                is_squeeze_cover = True

        # 价格有效位移
        eff = snap.features.get("directional_efficiency")
        if eff and eff.available and eff.value is not None:
            passed = eff.value >= self.min_efficiency
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="directional_efficiency",
                value=eff.value,
                threshold=self.min_efficiency,
                passed=passed,
            ))

        # 回吐检查
        retrace = snap.features.get("retrace_ratio")
        if retrace and retrace.available and retrace.value is not None:
            passed = retrace.value < self.max_retrace
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="retrace_acceptance",
                value=retrace.value,
                threshold=self.max_retrace,
                passed=passed,
            ))

        # 判断 SUSPECTED
        flow_passed = any(
            e.family == EvidenceFamily.FLOW and e.passed for e in evidence
        )
        suspected = (
            direction is not None
            and flow_passed
            and not is_squeeze_cover
            and confidence != ConfidenceState.UNKNOWN
        )

        # 判断 CONFIRMED — 需要 CONFIDENT + hold + 证据链完整
        all_passed = all(e.passed for e in evidence if e.family != EvidenceFamily.CONTEXT)
        confirmed = (
            suspected
            and confidence == ConfidenceState.CONFIDENT
            and hold_duration_s >= self.confirmation_hold_s
            and all_passed
            and len(evidence) >= 3
        )

        return StartupResult(
            suspected=suspected,
            confirmed=confirmed,
            direction=direction,
            evidence=evidence,
            is_squeeze_cover=is_squeeze_cover,
        )
