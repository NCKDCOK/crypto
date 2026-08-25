"""Confidence Engine — 独立数值置信度。

依据：V1.1 计划 §十五
- 置信度独立于机会分
- 机会分回答：这个机会本身好不好？
- 置信度回答：我们对这个判断有多大把握？
- 受 Data Health / Evidence completeness / Multi-window consistency / Missing source / stale source 影响
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.config import ScoringConfig
from src.domain import ConfidenceState, FeatureSnapshot

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceBreakdown:
    """置信度计算结果。"""

    confidence: float  # 0.0~1.0
    available: bool
    factors: dict[str, float] = field(default_factory=dict)
    penalties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 4),
            "confidence_pct": round(self.confidence * 100, 1),
            "available": self.available,
            "factors": {k: round(v, 4) for k, v in self.factors.items()},
            "penalties": self.penalties,
        }


class ConfidenceEngine:
    """独立置信度引擎。

    从数据健康 + 证据完整性 + 多窗口一致性计算数值置信度。
    """

    def __init__(self, cfg: ScoringConfig) -> None:
        self.cfg = cfg

    def compute(
        self,
        confidence_state: ConfidenceState,
        snap: FeatureSnapshot,
        evidence_count: int,
        sample_count: int = 0,
    ) -> ConfidenceBreakdown:
        """计算数值置信度。"""
        w = self.cfg
        penalties: list[str] = []
        factors: dict[str, float] = {}

        # 基础置信度
        conf = w.confidence_base
        factors["base"] = conf

        # 1. 数据健康影响
        if confidence_state == ConfidenceState.UNKNOWN:
            conf -= w.confidence_stale_penalty
            penalties.append("关键数据 STALE/FAIL")
            factors["stale_penalty"] = -w.confidence_stale_penalty
        elif confidence_state == ConfidenceState.DEGRADED:
            conf -= w.confidence_degraded_penalty
            penalties.append("部分数据降级")
            factors["degraded_penalty"] = -w.confidence_degraded_penalty

        # 2. 缺失数据源
        feats = snap.features
        missing_sources = []
        # OI 缺失
        oi_val = feats.get("oi_contracts")
        if oi_val is None or not oi_val.available:
            conf -= w.confidence_missing_source_penalty
            missing_sources.append("OI")
            factors["missing_oi"] = -w.confidence_missing_source_penalty

        # Funding 缺失
        funding_val = feats.get("funding")
        if funding_val is None or not funding_val.available:
            conf -= w.confidence_missing_source_penalty * 0.5
            missing_sources.append("Funding")
            factors["missing_funding"] = -w.confidence_missing_source_penalty * 0.5

        if missing_sources:
            penalties.append(f"缺失数据源: {', '.join(missing_sources)}")

        # 3. 证据完整性
        if evidence_count < w.confidence_min_evidence:
            conf -= w.confidence_low_evidence_penalty
            penalties.append(f"证据不足 ({evidence_count}/{w.confidence_min_evidence})")
            factors["low_evidence"] = -w.confidence_low_evidence_penalty

        # 4. 多窗口一致性（检查 1m/5m/15m/1h context 是否同向）
        contexts = []
        for iv in ("1m", "5m", "15m", "1h"):
            cv = feats.get(f"context_{iv}")
            if cv and cv.available and cv.value is not None:
                contexts.append(cv.value)
        if len(contexts) >= 3:
            pos = sum(1 for c in contexts if c > 0)
            neg = sum(1 for c in contexts if c < 0)
            consistency = max(pos, neg) / len(contexts)
            if consistency < 0.6:
                conf -= 0.05
                penalties.append("多周期方向不一致")
                factors["multi_window_inconsistency"] = -0.05

        # 5. 评分预热
        available = sample_count >= w.warmup_min_samples
        if not available:
            conf = 0.0
            penalties.append("数据预热中")

        conf = max(0.0, min(1.0, conf))

        return ConfidenceBreakdown(
            confidence=conf,
            available=available,
            factors=factors,
            penalties=penalties,
        )
