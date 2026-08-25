"""Location Engine — 位置判断（V1.2 §19）。

回答：方向判断对了以后，现在这个位置到底值不值得参与？

输出：
- location_score
- chase_safety
- distance_from_origin
- distance_to_resistance
- distance_to_support
- distance_to_poc
- distance_to_vwap

用户结论：位置偏高不建议追 / 合理处于首次回踩承接区
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clamp(v: float | None) -> float | None:
    if v is None:
        return None
    return max(0.0, min(100.0, v))


@dataclass
class LocationResult:
    location_score: float | None
    chase_safety: float | None
    distance_from_origin: float | None
    distance_to_resistance: float | None
    distance_to_support: float | None
    distance_to_poc: float | None
    distance_to_vwap: float | None
    classification: str  # high / reasonable / low / unknown
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location_score": self.location_score,
            "chase_safety": self.chase_safety,
            "distance_from_origin": self.distance_from_origin,
            "distance_to_resistance": self.distance_to_resistance,
            "distance_to_support": self.distance_to_support,
            "distance_to_poc": self.distance_to_poc,
            "distance_to_vwap": self.distance_to_vwap,
            "classification": self.classification,
            "label": self.label,
        }


class LocationEngine:
    """位置引擎。"""

    def __init__(
        self,
        chase_too_far_pct: float = 0.05,
        near_support_pct: float = 0.02,
        near_resistance_pct: float = 0.02,
    ) -> None:
        self.chase_too_far_pct = chase_too_far_pct
        self.near_support_pct = near_support_pct
        self.near_resistance_pct = near_resistance_pct

    def compute(
        self,
        current_price: float | None,
        fv: dict[str, Any],
        *,
        structure=None,
        volume_profile=None,
    ) -> LocationResult:
        """计算位置。

        Args:
            current_price: 当前价。
            fv: feature dict（price_return_5m, retrace_ratio, acceptance...）。
            structure: StructureResult（P13）。
            volume_profile: VolumeProfileResult（P14）。
        """
        if current_price is None:
            return LocationResult(None, None, None, None, None, None, None,
                                  "unknown", "数据不足", {})

        price_5m = fv.get("price_return_5m")
        retrace = fv.get("retrace_ratio")
        acceptance = fv.get("acceptance")

        # 距支撑/阻力
        dist_resistance = None
        dist_support = None
        if structure and structure.resistance is not None:
            dist_resistance = (structure.resistance - current_price) / current_price
        if structure and structure.support is not None:
            dist_support = (current_price - structure.support) / current_price

        # 距 POC / VWAP
        dist_poc = None
        dist_vwap = None
        if volume_profile and volume_profile.poc is not None:
            dist_poc = (current_price - volume_profile.poc) / current_price
        if structure and structure.vwap is not None:
            dist_vwap = (current_price - structure.vwap) / current_price

        # 距启动幅度
        dist_origin = abs(price_5m) if price_5m is not None else None

        # 追涨安全：距启动远 + 回撤大 → 不安全
        chase_safety = None
        if dist_origin is not None:
            chase_safety = _clamp(100.0 - dist_origin * 1000.0)
        if retrace is not None and chase_safety is not None:
            chase_safety = _clamp(chase_safety + (1.0 - retrace) * 30.0)

        # 位置分综合
        components = []
        # 距支撑近 → 位置好
        if dist_support is not None:
            components.append(_clamp(100.0 - abs(dist_support) * 1000.0))
        # 距阻力远 → 位置好
        if dist_resistance is not None:
            components.append(_clamp(50.0 + dist_resistance * 1000.0))
        # 距 POC 近 → 位置好
        if dist_poc is not None:
            components.append(_clamp(100.0 - abs(dist_poc) * 1000.0))
        # 回踩健康 → 位置好
        if retrace is not None:
            components.append(_clamp(100.0 - retrace * 100.0))
        # 距启动不远 → 位置好
        if dist_origin is not None:
            components.append(_clamp(100.0 - dist_origin * 1000.0))

        location_score = (sum(components) / len(components)) if components else None

        # 分类
        classification = "unknown"
        label = "数据不足，无法判断位置"
        if location_score is not None:
            if dist_origin is not None and dist_origin > self.chase_too_far_pct:
                classification = "high"
                label = "位置偏高，当前不建议追"
            elif dist_support is not None and abs(dist_support) < self.near_support_pct:
                classification = "reasonable"
                label = "位置合理，处于首次回踩承接区"
            elif retrace is not None and retrace < 0.3 and acceptance is not None and acceptance > 0.5:
                classification = "reasonable"
                label = "位置合理，回踩健康"
            elif location_score > 60:
                classification = "reasonable"
                label = "位置合理"
            else:
                classification = "low"
                label = "位置一般，谨慎参与"

        factors = {
            "current_price": current_price,
            "distance_from_origin": dist_origin,
            "distance_to_resistance": dist_resistance,
            "distance_to_support": dist_support,
            "distance_to_poc": dist_poc,
            "distance_to_vwap": dist_vwap,
            "retrace_ratio": retrace,
        }
        return LocationResult(
            location_score=_clamp(location_score),
            chase_safety=_clamp(chase_safety),
            distance_from_origin=dist_origin,
            distance_to_resistance=dist_resistance,
            distance_to_support=dist_support,
            distance_to_poc=dist_poc,
            distance_to_vwap=dist_vwap,
            classification=classification,
            label=label,
            factors=factors,
        )
