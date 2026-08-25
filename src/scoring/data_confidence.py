"""Data Confidence Engine — 数据可信度。

依据：V1.2 计划 §3.3 / §4 / §5
回答：当前用于判断的数据是否完整、新鲜、稳定？

与 signal_confirmation 严格分离：
- data_confidence 只看「数据本身好不好」（freshness / 缺失 / stale / queue lag）。
- signal_confirmation 看「判断证据够不够」（证据 / 多周期 / 突破 / 回踩 / 现货）。

§5 缺失数据规则：
- 缺失不得默认 50 分。
- 缺失源记入 missing，降低 coverage，降低 score。
- 关键数据严重缺失 → data_confidence 低 → signal_confirmation 禁止 strong_confirm。

范围：0 ~ 100（UI 显示为百分比，文案「数据可信」）。
它不是历史胜率。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.config import ScoringConfig
from src.domain import ConfidenceState, FeatureSnapshot

logger = logging.getLogger(__name__)

# 预期数据源（用于 coverage 计算）。Spot 在 P5 之前不可用属正常，不强制要求。
EXPECTED_SOURCES_CORE: tuple[str, ...] = ("aggtrade", "kline", "oi", "funding")
EXPECTED_SOURCES_OPT: tuple[str, ...] = ("spot",)


@dataclass
class DataConfidenceBreakdown:
    """数据可信度计算结果。"""

    score: float  # 0~100
    available: bool
    coverage: float  # 0~1，可用源 / 预期源
    missing: list[str] = field(default_factory=list)
    factors: dict[str, float] = field(default_factory=dict)
    penalties: list[str] = field(default_factory=list)

    @property
    def score_pct(self) -> float:
        return round(self.score, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1) if self.available else None,
            "score_pct": round(self.score, 1) if self.available else None,
            "available": self.available,
            "coverage": round(self.coverage, 3),
            "missing": list(self.missing),
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
            "penalties": list(self.penalties),
        }


class DataConfidenceEngine:
    """数据可信度引擎 — 只评估数据完整 / 新鲜 / 稳定。"""

    def __init__(self, cfg: ScoringConfig) -> None:
        self.cfg = cfg

    def compute(
        self,
        confidence_state: ConfidenceState,
        snap: FeatureSnapshot,
        sample_count: int = 0,
        *,
        spot_available: bool | None = None,
        queue_lag_ms: float | None = None,
    ) -> DataConfidenceBreakdown:
        """计算数据可信度。

        Args:
            confidence_state: 由关键流 health 派生的分类置信（CONFIDENT/DEGRADED/UNKNOWN）。
            snap: FeatureSnapshot。
            sample_count: 主窗口基线样本数（预热用）。
            spot_available: 现货数据是否可用（P5 之前默认 None=未知，不惩罚）。
            queue_lag_ms: 队列延迟（ms），可选。
        """
        w = self.cfg
        missing: list[str] = []
        factors: dict[str, float] = {}
        penalties: list[str] = []

        # 评分预热
        available = sample_count >= w.warmup_min_samples
        if not available:
            return DataConfidenceBreakdown(
                score=0.0, available=False, coverage=0.0,
                missing=list(EXPECTED_SOURCES_CORE), penalties=["数据预热中"],
            )

        score = float(w.data_confidence_base)
        factors["base"] = score

        feats = snap.features

        # 1. 关键流 freshness（confidence_state 由 watchdog 真实派生）
        if confidence_state == ConfidenceState.UNKNOWN:
            score -= w.data_confidence_unknown_penalty
            factors["stale_penalty"] = -w.data_confidence_unknown_penalty
            penalties.append("关键数据 STALE/FAIL")
        elif confidence_state == ConfidenceState.DEGRADED:
            score -= w.data_confidence_degraded_penalty
            factors["degraded_penalty"] = -w.data_confidence_degraded_penalty
            penalties.append("部分数据降级")

        # stale_flag（feature engine 综合判定）
        stale_fv = feats.get("stale_flag")
        if stale_fv and stale_fv.available and stale_fv.value and stale_fv.value > 0:
            score -= w.data_confidence_stale_penalty
            factors["stale_flag_penalty"] = -w.data_confidence_stale_penalty
            if "关键数据 STALE/FAIL" not in penalties:
                penalties.append("stale_flag 命中")

        # 2. 缺失数据源（不得默认 50；缺失即扣分 + 降 coverage）
        # OI
        oi_val = feats.get("oi_contracts")
        oi_ok = oi_val is not None and oi_val.available and oi_val.value is not None
        if not oi_ok:
            score -= w.data_confidence_missing_oi_penalty
            missing.append("oi")
            factors["missing_oi"] = -w.data_confidence_missing_oi_penalty

        # Funding
        funding_val = feats.get("funding")
        funding_ok = funding_val is not None and funding_val.available and funding_val.value is not None
        if not funding_ok:
            score -= w.data_confidence_missing_funding_penalty
            missing.append("funding")
            factors["missing_funding"] = -w.data_confidence_missing_funding_penalty

        # Kline context（至少一个周期可用）
        kline_ok = False
        for iv in ("1m", "5m", "15m", "1h"):
            cv = feats.get(f"context_{iv}")
            if cv and cv.available and cv.value is not None:
                kline_ok = True
                break
        if not kline_ok:
            score -= w.data_confidence_missing_kline_penalty
            missing.append("kline")
            factors["missing_kline"] = -w.data_confidence_missing_kline_penalty

        # Spot（P5 之前 spot_available=None → 不惩罚，仅 coverage 不含 spot）
        if spot_available is False:
            score -= w.data_confidence_missing_spot_penalty
            missing.append("spot")
            factors["missing_spot"] = -w.data_confidence_missing_spot_penalty

        # 3. queue lag（可选）
        if queue_lag_ms is not None and queue_lag_ms > w.data_confidence_queue_lag_penalty_ms:
            lag_pen = w.data_confidence_queue_lag_penalty
            score -= lag_pen
            factors["queue_lag"] = -lag_pen
            penalties.append(f"队列延迟 {queue_lag_ms:.0f}ms")

        # coverage：核心源可用比例（spot 为可选，可用则加分母）
        core_avail = sum(1 for s in EXPECTED_SOURCES_CORE if s not in missing)
        denom = len(EXPECTED_SOURCES_CORE)
        if spot_available is not None:
            denom += 1
            if spot_available:
                core_avail += 1
        coverage = core_avail / denom if denom else 0.0

        if missing:
            penalties.append(f"缺失数据源: {', '.join(missing)}")

        score = max(0.0, min(100.0, score))

        return DataConfidenceBreakdown(
            score=score,
            available=available,
            coverage=coverage,
            missing=missing,
            factors=factors,
            penalties=penalties,
        )
