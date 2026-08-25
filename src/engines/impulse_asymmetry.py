"""Impulse Asymmetry Engine — 多空推动效率评分（V1.2 §10）。

从 impulse 特征计算多头/空头推动效率分数 + 结论文案。
用户展示：
  多头推动效率：38
  空头推动效率：86
  结论：上涨过程费力，下跌过程明显更顺畅，短线空头动能占优。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ImpulseAsymmetryResult:
    upside_efficiency_score: float | None  # 0~100
    downside_efficiency_score: float | None  # 0~100
    dominant_side: str  # up / down / balanced / unknown
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "upside_efficiency_score": self.upside_efficiency_score,
            "downside_efficiency_score": self.downside_efficiency_score,
            "dominant_side": self.dominant_side,
            "label": self.label,
            "factors": self.factors,
        }


def _eff_to_score(eff: float | None, scale: float = 1e6) -> float | None:
    """效率值（velocity/volume，量级很小）映射到 0~100。"""
    if eff is None:
        return None
    import math
    # 对数压缩 + clamp
    if eff <= 0:
        return 0.0
    s = 100.0 / (1.0 + math.exp(-math.log10(eff * scale + 1e-12) * 1.5 + 2.0))
    return max(0.0, min(100.0, s))


class ImpulseAsymmetryEngine:
    """多空推动效率引擎。"""

    def __init__(self, dominant_ratio: float = 1.5) -> None:
        self.dominant_ratio = dominant_ratio

    def compute(self, fv: dict[str, Any]) -> ImpulseAsymmetryResult:
        up_eff = fv.get("upside_volume_efficiency")
        down_eff = fv.get("downside_volume_efficiency")
        impulse_ratio = fv.get("impulse_ratio")

        up_score = _eff_to_score(up_eff)
        down_score = _eff_to_score(down_eff)

        factors = {
            "upside_velocity": fv.get("upside_velocity"),
            "downside_velocity": fv.get("downside_velocity"),
            "upside_volume_efficiency": up_eff,
            "downside_volume_efficiency": down_eff,
            "impulse_ratio": impulse_ratio,
        }

        if up_score is None and down_score is None:
            return ImpulseAsymmetryResult(
                None, None, "unknown", "数据不足，无法判断多空推动效率", factors
            )

        # 判定主导方（用原始效率比，避免分数饱和失真）
        if up_eff is not None and down_eff is not None and up_eff > 0:
            ratio = down_eff / up_eff
            if ratio > self.dominant_ratio:
                dominant = "down"
                label = "上涨过程费力，下跌过程明显更顺畅，短线空头动能占优。"
            elif ratio < 1.0 / self.dominant_ratio:
                dominant = "up"
                label = "下跌过程费力，上涨过程明显更顺畅，短线多头动能占优。"
            else:
                dominant = "balanced"
                label = "多空推动效率接近，暂无明显主导方。"
        elif up_eff is not None and down_eff is None:
            dominant = "up"
            label = "仅有上涨段数据，多头推动占观察。"
        elif down_eff is not None and up_eff is None:
            dominant = "down"
            label = "仅有下跌段数据，空头推动占观察。"
        else:
            dominant = "balanced"
            label = "多空推动效率接近，暂无明显主导方。"

        return ImpulseAsymmetryResult(up_score, down_score, dominant, label, factors)
