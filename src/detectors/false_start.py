"""False Start Filter — V1 的核心 Edge。

依据：ANALYSIS_MODEL.md §4, STATE_MACHINE.md T4
7 个 Veto：
- data_stale (hard)
- rapid_retrace (hard)
- oi_contraction (hard, 非 squeeze 例外)
- delta_reversal (hard)
- no_acceptance (hard)
- low_efficiency_absorption (soft)
- crowding_extreme (soft)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from src.domain import Direction, Evidence, FeatureSnapshot, Veto, VetoSeverity, VetoType


@dataclass
class FalseStartResult:
    """False Start 检测结果。"""

    rejected: bool  # 是否被拒绝（hard veto 命中）
    vetoes: list[Veto] = field(default_factory=list)
    reason: str | None = None


class FalseStartFilter:
    """False Start Filter — 7 个 veto。"""

    def __init__(
        self,
        rapid_retrace_threshold: float = 0.7,  # 回吐 > 70% → hard veto
        absorption_flow_impact_threshold: float = 0.001,  # flow_impact 极低
        absorption_delta_threshold: float = 10000.0,  # delta 大但推不动
        crowding_percentile_threshold: float = 95.0,  # funding/premium 95+ 百分位
    ) -> None:
        self.rapid_retrace_threshold = rapid_retrace_threshold
        self.absorption_flow_impact_threshold = absorption_flow_impact_threshold
        self.absorption_delta_threshold = absorption_delta_threshold
        self.crowding_percentile_threshold = crowding_percentile_threshold

    def check(
        self,
        snap: FeatureSnapshot,
        direction: Direction | None = None,
        is_confident: bool = True,
    ) -> FalseStartResult:
        """检查所有 veto。

        Args:
            snap: FeatureSnapshot
            direction: 当前推断方向
            is_confident: confidence_state 是否 CONFIDENT

        Returns:
            FalseStartResult
        """
        vetoes: list[Veto] = []
        rejected = False

        # 1. data_stale (hard) — 关键输入 stale
        data_stale = not is_confident
        vetoes.append(Veto(
            type=VetoType.DATA_STALE,
            triggered=data_stale,
            severity=VetoSeverity.HARD,
        ))
        if data_stale:
            rejected = True

        # 2. rapid_retrace (hard)
        retrace = snap.features.get("retrace_ratio")
        retrace_triggered = (
            retrace and retrace.available and retrace.value is not None
            and retrace.value > self.rapid_retrace_threshold
        )
        vetoes.append(Veto(
            type=VetoType.RAPID_RETRACE,
            triggered=bool(retrace_triggered),
            severity=VetoSeverity.HARD,
        ))
        if retrace_triggered:
            rejected = True

        # 3. oi_contraction (hard) — 上涨但 OI 收缩 → squeeze/cover
        oi_change = snap.features.get("oi_change_1m")
        oi_contraction_triggered = (
            direction == Direction.LONG
            and oi_change and oi_change.available and oi_change.value is not None
            and oi_change.value < 0
        )
        vetoes.append(Veto(
            type=VetoType.OI_CONTRACTION,
            triggered=bool(oi_contraction_triggered),
            severity=VetoSeverity.HARD,
        ))
        if oi_contraction_triggered:
            rejected = True

        # 4. delta_reversal (hard) — 初始同向 delta 后持续反向
        # 简化：检查 taker_delta 方向与 direction 是否相反
        delta = snap.features.get("taker_delta")
        delta_reversal_triggered = (
            direction is not None
            and delta and delta.available and delta.value is not None
            and (
                (direction == Direction.LONG and delta.value < 0)
                or (direction == Direction.SHORT and delta.value > 0)
            )
        )
        vetoes.append(Veto(
            type=VetoType.DELTA_REVERSAL,
            triggered=bool(delta_reversal_triggered),
            severity=VetoSeverity.HARD,
        ))
        if delta_reversal_triggered:
            rejected = True

        # 5. no_acceptance (hard) — directional_efficiency 极低
        eff = snap.features.get("directional_efficiency")
        no_acceptance_triggered = (
            eff and eff.available and eff.value is not None
            and eff.value < 0.05  # 效率极低
        )
        vetoes.append(Veto(
            type=VetoType.NO_ACCEPTANCE,
            triggered=bool(no_acceptance_triggered),
            severity=VetoSeverity.HARD,
        ))
        if no_acceptance_triggered:
            rejected = True

        # 6. low_efficiency_absorption (soft) — delta 大但 flow_impact 极低
        flow_impact = snap.features.get("flow_impact")
        absorption_triggered = (
            delta and delta.available and delta.value is not None
            and abs(delta.value) > self.absorption_delta_threshold
            and flow_impact and flow_impact.available and flow_impact.value is not None
            and abs(flow_impact.value) < self.absorption_flow_impact_threshold
        )
        vetoes.append(Veto(
            type=VetoType.LOW_EFFICIENCY_ABSORPTION,
            triggered=bool(absorption_triggered),
            severity=VetoSeverity.SOFT,
        ))

        # 7. crowding_extreme (soft) — funding/premium 极端
        funding_pct = snap.features.get("funding_percentile")
        crowding_triggered = (
            funding_pct and funding_pct.available and funding_pct.value is not None
            and funding_pct.value > self.crowding_percentile_threshold
        )
        vetoes.append(Veto(
            type=VetoType.CROWDING_EXTREME,
            triggered=bool(crowding_triggered),
            severity=VetoSeverity.SOFT,
        ))

        return FalseStartResult(
            rejected=rejected,
            vetoes=vetoes,
        )
