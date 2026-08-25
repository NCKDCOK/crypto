"""Signal Confirmation Engine — 信号确认度。

依据：V1.2 计划 §3.2 / §4 / §5 / §22
回答：这次判断有多少关键证据已经得到确认？

构成因素：
- 核心 Evidence 通过数量（OI 持续扩张 / 主动买盘持续占优 / CVD 保持上行）
- 辅助 Evidence 通过数量（成交量异常 / 突破有效性 / 回踩承接 / 15m 同向 / 现货确认）
- 多周期一致性（1m/5m/15m/1h）
- Breakout Acceptance / Retest Confirmation（P12 注入）
- Setup 完整程度（P11 注入）
- False Start Veto 是否通过

§5 缺失数据规则：
- 缺失证据项不得默认通过，也不得默认 50；从有效分母移除。
- 核心组件缺失时禁止 strong_confirm。

它不是历史成功率。UI 必须标「确认度」而非「胜率」。
范围：0 ~ 100。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.config import ScoringConfig
from src.domain import FeatureSnapshot

logger = logging.getLogger(__name__)


@dataclass
class ConfirmationContext:
    """信号确认上下文 — 可扩展。

    V1.2 Phase1 仅填充 direction/evidence_count/veto_count/breakout_acceptance。
    后续 Phase 注入更多字段：
      - breakout_hold / retest_confirmed (P12 Breakout Lifecycle)
      - spot_perp_agreement (P6 Spot×Perp)
      - setup_complete (P11 Setup Type)
    缺失字段为 None → 从有效分母移除（§5）。
    """

    direction: str | None
    evidence_count: int = 0
    veto_count: int = 0  # 触发的 hard veto 数量
    breakout_acceptance: float | None = None  # 来自 feature acceptance
    # 后续 Phase 注入（默认 None = 不可用）
    breakout_hold: bool | None = None
    retest_confirmed: bool | None = None
    spot_perp_agreement: float | None = None
    setup_complete: bool | None = None


@dataclass
class SignalConfirmationBreakdown:
    """信号确认度计算结果。"""

    score: float  # 0~100
    available: bool
    core_passed: int = 0
    core_total: int = 0
    supporting_passed: int = 0
    supporting_total: int = 0
    veto_passed: bool = True
    multi_tf_aligned: int = 0
    multi_tf_total: int = 0
    strong_confirm: bool = False
    factors: dict[str, float] = field(default_factory=dict)

    @property
    def score_pct(self) -> float:
        return round(self.score, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1) if self.available else None,
            "score_pct": round(self.score, 1) if self.available else None,
            "available": self.available,
            "core_passed": self.core_passed,
            "core_total": self.core_total,
            "supporting_passed": self.supporting_passed,
            "supporting_total": self.supporting_total,
            "veto_passed": self.veto_passed,
            "multi_tf_aligned": self.multi_tf_aligned,
            "multi_tf_total": self.multi_tf_total,
            "strong_confirm": self.strong_confirm,
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
        }


def _sign(direction: str | None) -> float:
    if direction == "LONG":
        return 1.0
    if direction == "SHORT":
        return -1.0
    return 0.0


class SignalConfirmationEngine:
    """信号确认度引擎 — 评估判断证据的确认程度。"""

    def __init__(self, cfg: ScoringConfig) -> None:
        self.cfg = cfg

    def compute(
        self,
        snap: FeatureSnapshot,
        ctx: ConfirmationContext,
        sample_count: int = 0,
        *,
        data_confidence_score: float | None = None,
    ) -> SignalConfirmationBreakdown:
        """计算信号确认度。

        Args:
            snap: FeatureSnapshot。
            ctx: ConfirmationContext（方向 / 证据数 / veto / 可选注入）。
            sample_count: 主窗口基线样本数（预热）。
            data_confidence_score: 数据可信度分数，用于 strong_confirm 门控。
        """
        w = self.cfg
        available = sample_count >= w.warmup_min_samples
        if not available:
            return SignalConfirmationBreakdown(score=0.0, available=False)

        feats = snap.features
        fv = {k: (v.value if v.available else None) for k, v in feats.items()}
        sign = _sign(ctx.direction)

        # ── 核心证据（3 项，方向对齐）──
        core_items: list[tuple[str, bool]] = []

        # OI 持续扩张（与方向同向）
        oi_5m = fv.get("oi_change_5m")
        if oi_5m is not None and sign != 0:
            core_items.append(("oi_expansion", oi_5m * sign > 0))
        elif oi_5m is not None:
            core_items.append(("oi_expansion", oi_5m > 0))

        # 主动买盘持续占优（signed_delta / taker_delta 与方向同向）
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        if delta is not None and sign != 0:
            core_items.append(("taker_delta", delta * sign > 0))
        elif delta is not None:
            core_items.append(("taker_delta", delta > 0))

        # CVD 保持上行（cvd_slope_z 与方向同向）
        cvd_z = fv.get("cvd_slope_z")
        if cvd_z is not None and sign != 0:
            core_items.append(("cvd_slope", cvd_z * sign > 0))
        elif cvd_z is not None:
            core_items.append(("cvd_slope", cvd_z > 0))

        core_total = len(core_items)
        core_passed = sum(1 for _, p in core_items if p)

        # ── 辅助证据（5 项）──
        sup_items: list[tuple[str, bool]] = []

        # 成交量异常
        vol_z = fv.get("volume_z")
        if vol_z is not None:
            sup_items.append(("volume_anomaly", vol_z >= w.sc_volume_z_threshold))

        # 突破有效性 / 突破保持（优先用 breakout_hold，否则 acceptance）
        if ctx.breakout_hold is not None:
            sup_items.append(("breakout_hold", bool(ctx.breakout_hold)))
        else:
            accept = ctx.breakout_acceptance
            if accept is None:
                accept = fv.get("acceptance")
            if accept is not None:
                sup_items.append(("breakout_acceptance", accept >= w.sc_acceptance_threshold))

        # 回踩承接（优先用 retest_confirmed，否则 retrace_ratio）
        if ctx.retest_confirmed is not None:
            sup_items.append(("retest_confirmed", bool(ctx.retest_confirmed)))
        else:
            retrace = fv.get("retrace_ratio")
            if retrace is not None:
                sup_items.append(("retrace_healthy", retrace <= w.sc_retrace_healthy))

        # 15m 同向
        ctx_15m = fv.get("context_15m")
        if ctx_15m is not None and sign != 0:
            sup_items.append(("tf_15m_aligned", ctx_15m * sign > 0))
        elif ctx_15m is not None:
            sup_items.append(("tf_15m_aligned", ctx_15m > 0))

        # 现货确认（P6 注入 spot_perp_agreement）
        if ctx.spot_perp_agreement is not None:
            sup_items.append(("spot_perp_agreement", ctx.spot_perp_agreement >= w.sc_spot_agreement_threshold))

        sup_total = len(sup_items)
        sup_passed = sum(1 for _, p in sup_items if p)

        # ── 多周期一致性（1m/5m/15m/1h）──
        tf_values = [fv.get(f"context_{iv}") for iv in ("1m", "5m", "15m", "1h")]
        tf_values = [v for v in tf_values if v is not None]
        multi_tf_total = len(tf_values)
        multi_tf_aligned = 0
        if multi_tf_total >= 2 and sign != 0:
            aligned = sum(1 for v in tf_values if v * sign > 0)
            multi_tf_aligned = aligned
        elif multi_tf_total >= 2:
            pos = sum(1 for v in tf_values if v > 0)
            multi_tf_aligned = max(pos, multi_tf_total - pos)

        # ── Veto（False Start）──
        veto_passed = ctx.veto_count == 0

        # ── 加权计分（缺失项从分母移除，§5）──
        cw = w.sc_core_weight
        sw = w.sc_supporting_weight
        vw = w.sc_veto_weight
        mw = w.sc_multitf_weight
        wsum = cw + sw + vw + mw

        core_part = (core_passed / core_total * 100.0) if core_total > 0 else 0.0
        sup_part = (sup_passed / sup_total * 100.0) if sup_total > 0 else 0.0
        veto_part = 100.0 if veto_passed else 0.0
        mt_part = (multi_tf_aligned / multi_tf_total * 100.0) if multi_tf_total > 0 else 0.0

        score = (
            core_part * cw
            + sup_part * sw
            + veto_part * vw
            + mt_part * mw
        ) / wsum if wsum > 0 else 0.0
        score = max(0.0, min(100.0, score))

        # ── strong_confirm 门控 ──
        # §5: 核心组件缺失（core_total < 3）时禁止强确认
        # §15.5: 5m 突破确认 + 15m 同向 + 1h 不逆向
        strong = False
        if core_total >= 1 and core_passed == core_total and veto_passed:
            dc_ok = (data_confidence_score is None) or (data_confidence_score >= w.sc_strong_confirm_min_dc)
            # 多周期：至少 2 个周期对齐
            tf_ok = multi_tf_total >= 2 and multi_tf_aligned >= 2
            if dc_ok and tf_ok:
                strong = True

        factors = {
            "core_ratio": core_part,
            "supporting_ratio": sup_part,
            "veto_passed": veto_part,
            "multitf_ratio": mt_part,
        }

        return SignalConfirmationBreakdown(
            score=score,
            available=available,
            core_passed=core_passed,
            core_total=core_total,
            supporting_passed=sup_passed,
            supporting_total=sup_total,
            veto_passed=veto_passed,
            multi_tf_aligned=multi_tf_aligned,
            multi_tf_total=multi_tf_total,
            strong_confirm=strong,
            factors=factors,
        )
