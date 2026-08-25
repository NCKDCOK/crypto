"""Startup Detector — 建立"新增资金推动"证据链。

依据：ANALYSIS_MODEL.md §5.2, STATE_MACHINE.md T3/T5, 改造任务文档 §15
证据链：候选方向 + 量异常 + 主动成交同向 + OI 同向扩张（或 squeeze 例外）
       + 价格有效位移 + 突破后未全部回吐 + price_efficiency 正常
→ SUSPECTED_START / START_CONFIRMED

subtype 区分（§15）：
- new_long_build:   Price↑ OI↑ Delta↑ （新增多头）
- short_squeeze:    Price↑ OI↓ Delta↑ （空头回补/逼空，非新增多头）
- new_short_build:  Price↓ OI↑ Delta↓ （新增空头）
- long_liquidation: Price↓ OI↓ Delta↓ （多头被清算）
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    subtype: str | None = None  # new_long_build / short_squeeze / new_short_build / long_liquidation
    reason: str | None = None


def _fv(snap: FeatureSnapshot, key: str) -> float | None:
    v = snap.features.get(key)
    if v and v.available and v.value is not None:
        return v.value
    return None


class StartupDetector:
    """Startup Detector — 从 anomaly 候选建立方向 + 新增资金 + 价格效果证据链。"""

    def __init__(
        self,
        confirmation_hold_s: float = 15.0,
        oi_expansion_threshold: float = 0.0,  # OI 变化 > 0 视为扩张
        min_efficiency: float = 0.2,  # 最低 directional efficiency
        max_retrace: float = 0.8,  # 最大回吐比例
        min_evidence: int = 3,
    ) -> None:
        self.confirmation_hold_s = confirmation_hold_s
        self.oi_expansion_threshold = oi_expansion_threshold
        self.min_efficiency = min_efficiency
        self.max_retrace = max_retrace
        self.min_evidence = min_evidence

    def detect(
        self,
        snap: FeatureSnapshot,
        anomaly: AnomalyResult,
        confidence: ConfidenceState = ConfidenceState.CONFIDENT,
        hold_duration_s: float = 0.0,
    ) -> StartupResult:
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
        subtype: str | None = None

        # 方向从 taker_delta / cvd_slope_z 推断
        delta = _fv(snap, "taker_delta")
        if delta is not None:
            if delta > 0:
                direction = Direction.LONG
            elif delta < 0:
                direction = Direction.SHORT
            evidence.append(Evidence(
                family=EvidenceFamily.FLOW,
                type="taker_delta",
                value=delta,
                passed=delta != 0,
            ))

        # OI 同向扩张检查
        oi_change = _fv(snap, "oi_change_1m")
        oi_expanding = None
        if oi_change is not None and direction is not None:
            oi_expanding = oi_change > self.oi_expansion_threshold
            evidence.append(Evidence(
                family=EvidenceFamily.POSITION,
                type="oi_expansion" if oi_expanding else "oi_contraction_cover",
                value=oi_change,
                passed=bool(oi_expanding),
            ))

        # subtype 分类（§15）
        if direction is not None and oi_change is not None and delta is not None:
            oi_up = oi_change > self.oi_expansion_threshold
            delta_up = delta > 0
            if direction == Direction.LONG:
                if oi_up and delta_up:
                    subtype = "new_long_build"
                elif (not oi_up) and delta_up:
                    subtype = "short_squeeze"
                    is_squeeze_cover = True
            else:  # SHORT
                if oi_up and (not delta_up):
                    subtype = "new_short_build"
                elif (not oi_up) and (not delta_up):
                    subtype = "long_liquidation"

        # 价格有效位移
        eff = _fv(snap, "directional_efficiency")
        if eff is not None:
            passed = eff >= self.min_efficiency
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="directional_efficiency",
                value=eff,
                threshold=self.min_efficiency,
                passed=passed,
            ))

        # price_efficiency（资金推动效率，§12）
        pe = _fv(snap, "price_efficiency")
        if pe is not None:
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="price_efficiency",
                value=pe,
                passed=pe > 0,
            ))

        # acceptance（突破后站稳）
        acc = _fv(snap, "acceptance")
        if acc is not None:
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="breakout_acceptance",
                value=acc,
                passed=acc >= 0.5,
            ))

        # 回吐检查
        retrace = _fv(snap, "retrace_ratio")
        if retrace is not None:
            passed = retrace < self.max_retrace
            evidence.append(Evidence(
                family=EvidenceFamily.PRICE_EFFECT,
                type="retrace_acceptance",
                value=retrace,
                threshold=self.max_retrace,
                passed=passed,
            ))

        # CVD 同向
        cvd_slope_z = _fv(snap, "cvd_slope_z")
        if cvd_slope_z is not None and direction is not None:
            cvd_same = (
                (direction == Direction.LONG and cvd_slope_z > 0)
                or (direction == Direction.SHORT and cvd_slope_z < 0)
            )
            evidence.append(Evidence(
                family=EvidenceFamily.FLOW,
                type="cvd_slope_z",
                value=cvd_slope_z,
                passed=cvd_same,
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
            and len(evidence) >= self.min_evidence
        )

        return StartupResult(
            suspected=suspected,
            confirmed=confirmed,
            direction=direction,
            evidence=evidence,
            is_squeeze_cover=is_squeeze_cover,
            subtype=subtype,
        )
