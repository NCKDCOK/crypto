"""False Start Filter — V1 的核心 Edge。

依据：ANALYSIS_MODEL.md §4, STATE_MACHINE.md T4, 改造任务文档 §16
8 个 Veto：
- data_stale (hard) — 关键输入 stale（读 FeatureSnapshot.data_health / stale_flag）
- rapid_retrace (hard)
- oi_contraction (hard, 非 squeeze 例外)
- delta_reversal (hard)
- no_acceptance (hard)
- low_efficiency_absorption (soft)
- crowding_extreme (soft)
- one_bar_spike (hard) — 只有单根 spike，无延续

每个 Veto 命中/未命中都记录 detail（值/阈值/窗口/原因），不得只输出 Rejected。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain import Direction, FeatureSnapshot, HealthLevel, Veto, VetoSeverity, VetoType


@dataclass
class FalseStartResult:
    """False Start 检测结果。"""

    rejected: bool  # 是否被拒绝（hard veto 命中）
    vetoes: list[Veto] = field(default_factory=list)
    reason: str | None = None


def _fv(snap: FeatureSnapshot, key: str) -> float | None:
    v = snap.features.get(key)
    if v and v.available and v.value is not None:
        return v.value
    return None


class FalseStartFilter:
    """False Start Filter — 8 个 veto，每个均带解释性 detail。"""

    def __init__(
        self,
        rapid_retrace_threshold: float = 0.7,  # 回吐 > 70% → hard veto
        absorption_flow_impact_threshold: float = 0.001,  # flow_impact 极低
        absorption_delta_threshold: float = 10000.0,  # delta 大但推不动
        crowding_percentile_threshold: float = 95.0,  # funding/premium 95+ 百分位
        one_bar_spike_retrace: float = 0.6,  # 5s spike 后回吐 > 60% → 单根 spike
    ) -> None:
        self.rapid_retrace_threshold = rapid_retrace_threshold
        self.absorption_flow_impact_threshold = absorption_flow_impact_threshold
        self.absorption_delta_threshold = absorption_delta_threshold
        self.crowding_percentile_threshold = crowding_percentile_threshold
        self.one_bar_spike_retrace = one_bar_spike_retrace

    def check(
        self,
        snap: FeatureSnapshot,
        direction: Direction | None = None,
        is_confident: bool = True,
    ) -> FalseStartResult:
        """检查所有 veto。

        Args:
            snap: FeatureSnapshot（含 data_health / stale_flag）
            direction: 当前推断方向
            is_confident: confidence_state 是否 CONFIDENT（兜底；优先读 data_health）
        """
        vetoes: list[Veto] = []
        rejected = False

        # 1. data_stale (hard) — 优先读真实 data_health / stale_flag
        stale_flag = _fv(snap, "stale_flag")
        data_stale = bool(stale_flag and stale_flag >= 1.0)
        if not data_stale:
            # 兜底：若 is_confient=False 也视为 stale
            data_stale = not is_confident
        unhealthy_streams = [
            s for s, lvl in snap.data_health.items()
            if lvl in (HealthLevel.STALE.value, HealthLevel.DRIFT.value, HealthLevel.FAIL.value)
        ]
        vetoes.append(Veto(
            type=VetoType.DATA_STALE,
            triggered=data_stale,
            severity=VetoSeverity.HARD,
            detail={
                "stale_flag": stale_flag,
                "unhealthy_streams": unhealthy_streams,
                "reason": "critical_input_stale" if data_stale else "ok",
            },
        ))
        if data_stale:
            rejected = True

        # 2. rapid_retrace (hard)
        retrace = _fv(snap, "retrace_ratio")
        retrace_triggered = retrace is not None and retrace > self.rapid_retrace_threshold
        vetoes.append(Veto(
            type=VetoType.RAPID_RETRACE,
            triggered=retrace_triggered,
            severity=VetoSeverity.HARD,
            detail={
                "retrace_ratio": retrace,
                "threshold": self.rapid_retrace_threshold,
                "window": "30s",
                "reason": "retrace_exceeds_threshold" if retrace_triggered else "ok",
            },
        ))
        if retrace_triggered:
            rejected = True

        # 3. oi_contraction (hard) — 上涨但 OI 收缩 → squeeze/cover（非 squeeze 例外才否决）
        oi_change = _fv(snap, "oi_change_1m")
        oi_contraction_triggered = (
            direction == Direction.LONG
            and oi_change is not None
            and oi_change < 0
        )
        vetoes.append(Veto(
            type=VetoType.OI_CONTRACTION,
            triggered=oi_contraction_triggered,
            severity=VetoSeverity.HARD,
            detail={
                "oi_change_1m": oi_change,
                "direction": direction.value if direction else None,
                "window": "1m",
                "reason": "long_but_oi_contracting" if oi_contraction_triggered else "ok",
            },
        ))
        if oi_contraction_triggered:
            rejected = True

        # 4. delta_reversal (hard)
        delta = _fv(snap, "taker_delta")
        delta_reversal_triggered = (
            direction is not None
            and delta is not None
            and (
                (direction == Direction.LONG and delta < 0)
                or (direction == Direction.SHORT and delta > 0)
            )
        )
        vetoes.append(Veto(
            type=VetoType.DELTA_REVERSAL,
            triggered=delta_reversal_triggered,
            severity=VetoSeverity.HARD,
            detail={
                "taker_delta": delta,
                "direction": direction.value if direction else None,
                "window": "30s",
                "reason": "delta_opposite_to_direction" if delta_reversal_triggered else "ok",
            },
        ))
        if delta_reversal_triggered:
            rejected = True

        # 5. no_acceptance (hard) — directional_efficiency 极低 或 acceptance 极低
        eff = _fv(snap, "directional_efficiency")
        acceptance = _fv(snap, "acceptance")
        no_acceptance_triggered = (
            (eff is not None and eff < 0.05)
            or (acceptance is not None and acceptance < 0.3)
        )
        vetoes.append(Veto(
            type=VetoType.NO_ACCEPTANCE,
            triggered=no_acceptance_triggered,
            severity=VetoSeverity.HARD,
            detail={
                "directional_efficiency": eff,
                "acceptance": acceptance,
                "window": "30s",
                "reason": "no_acceptance_after_breakout" if no_acceptance_triggered else "ok",
            },
        ))
        if no_acceptance_triggered:
            rejected = True

        # 6. low_efficiency_absorption (soft) — delta 大但 flow_impact 极低
        flow_impact = _fv(snap, "flow_impact")
        absorption_triggered = (
            delta is not None and abs(delta) > self.absorption_delta_threshold
            and flow_impact is not None and abs(flow_impact) < self.absorption_flow_impact_threshold
        )
        vetoes.append(Veto(
            type=VetoType.LOW_EFFICIENCY_ABSORPTION,
            triggered=absorption_triggered,
            severity=VetoSeverity.SOFT,
            detail={
                "taker_delta": delta,
                "flow_impact": flow_impact,
                "delta_threshold": self.absorption_delta_threshold,
                "flow_impact_threshold": self.absorption_flow_impact_threshold,
                "reason": "large_flow_no_price_progress" if absorption_triggered else "ok",
            },
        ))

        # 7. crowding_extreme (soft)
        funding_pct = _fv(snap, "funding_percentile")
        premium_pct = _fv(snap, "premium_percentile")
        crowding_triggered = (
            (funding_pct is not None and funding_pct > self.crowding_percentile_threshold)
            or (premium_pct is not None and premium_pct > self.crowding_percentile_threshold)
        )
        vetoes.append(Veto(
            type=VetoType.CROWDING_EXTREME,
            triggered=crowding_triggered,
            severity=VetoSeverity.SOFT,
            detail={
                "funding_percentile": funding_pct,
                "premium_percentile": premium_pct,
                "threshold": self.crowding_percentile_threshold,
                "reason": "extreme_crowding" if crowding_triggered else "ok",
            },
        ))

        # 8. one_bar_spike (hard) — 5s 内剧烈位移但 30s retrace 大 → 单根 spike 无延续
        ret5 = _fv(snap, "price_return_5s")
        ret30 = _fv(snap, "price_return_30s")
        one_bar_triggered = False
        if ret5 is not None and ret30 is not None and abs(ret5) > 0:
            # 5s 位移占 30s 位移绝大部分，且 30s 已大幅回吐
            ratio_5_to_30 = abs(ret5) / max(abs(ret30), 1e-9)
            one_bar_triggered = (
                ratio_5_to_30 > 2.0
                and retrace is not None
                and retrace > self.one_bar_spike_retrace
            )
        vetoes.append(Veto(
            type=VetoType.ONE_BAR_SPIKE,
            triggered=one_bar_triggered,
            severity=VetoSeverity.HARD,
            detail={
                "price_return_5s": ret5,
                "price_return_30s": ret30,
                "retrace_ratio": retrace,
                "retrace_threshold": self.one_bar_spike_retrace,
                "window": "5s/30s",
                "reason": "single_spike_no_continuation" if one_bar_triggered else "ok",
            },
        ))
        if one_bar_triggered:
            rejected = True

        return FalseStartResult(rejected=rejected, vetoes=vetoes)
