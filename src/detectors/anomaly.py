"""Anomaly Detector — 高召回发现异动候选。

依据：ANALYSIS_MODEL.md §5.1, STATE_MACHINE.md T1/T2
- 输入：VolumeZ/TradeCountZ/PriceAccelZ/TakerDeltaZ
- 输出：AnomalyEvidence 列表 + direction_hint（可为空）
- 不输出 LONG/SHORT 决策
- 关键数据 stale → 不得发可升级到 confirmed 的 anomaly
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from src.domain import ConfidenceState, Evidence, EvidenceFamily, FeatureSnapshot


@dataclass
class AnomalyResult:
    """Anomaly 检测结果。"""

    is_anomaly: bool
    evidence: list[Evidence] = field(default_factory=list)
    direction_hint: str | None = None  # 可为空


class AnomalyDetector:
    """Anomaly Detector。

    基于 robust z-score，高召回。不判断方向。
    所有阈值来自配置。
    """

    def __init__(
        self,
        volume_z_threshold: float = 3.0,
        trade_count_z_threshold: float = 3.0,
        price_accel_z_threshold: float = 2.5,
        taker_delta_z_threshold: float = 2.5,
    ) -> None:
        self.volume_z_threshold = volume_z_threshold
        self.trade_count_z_threshold = trade_count_z_threshold
        self.price_accel_z_threshold = price_accel_z_threshold
        self.taker_delta_z_threshold = taker_delta_z_threshold

    def detect(
        self,
        snap: FeatureSnapshot,
        confidence: ConfidenceState = ConfidenceState.CONFIDENT,
    ) -> AnomalyResult:
        """检测异常。

        Args:
            snap: FeatureSnapshot
            confidence: 当前 ConfidenceState

        Returns:
            AnomalyResult
        """
        evidence: list[Evidence] = []
        direction_hint: str | None = None

        # stale 时不得发可升级到 confirmed 的 anomaly
        if confidence == ConfidenceState.UNKNOWN:
            return AnomalyResult(is_anomaly=False, evidence=[], direction_hint=None)

        # Volume Z
        vol_z = snap.features.get("volume_z")
        if vol_z and vol_z.available and vol_z.value is not None:
            passed = abs(vol_z.value) >= self.volume_z_threshold
            evidence.append(Evidence(
                family=EvidenceFamily.ANOMALY,
                type="volume_z",
                window=vol_z.window,
                value=vol_z.value,
                threshold=self.volume_z_threshold,
                passed=passed,
            ))

        # Trade Count Z
        tc_z = snap.features.get("trade_count_z")
        if tc_z and tc_z.available and tc_z.value is not None:
            passed = abs(tc_z.value) >= self.trade_count_z_threshold
            evidence.append(Evidence(
                family=EvidenceFamily.ANOMALY,
                type="trade_count_z",
                window=tc_z.window,
                value=tc_z.value,
                threshold=self.trade_count_z_threshold,
                passed=passed,
            ))

        # Taker Delta Z (通过 cvd_slope_z 近似)
        delta_z = snap.features.get("cvd_slope_z")
        if delta_z and delta_z.available and delta_z.value is not None:
            passed = abs(delta_z.value) >= self.taker_delta_z_threshold
            evidence.append(Evidence(
                family=EvidenceFamily.ANOMALY,
                type="taker_delta_z",
                window=delta_z.window,
                value=delta_z.value,
                threshold=self.taker_delta_z_threshold,
                passed=passed,
            ))
            if passed:
                direction_hint = "LONG" if delta_z.value > 0 else "SHORT"

        is_anomaly = any(e.passed for e in evidence)

        return AnomalyResult(
            is_anomaly=is_anomaly,
            evidence=evidence,
            direction_hint=direction_hint,
        )
