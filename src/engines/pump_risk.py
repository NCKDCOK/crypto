"""Pump Risk Engine — 异常拉升风险（V1.2 §41）。

证据：
- 短时间极端涨幅
- 极端 Volume
- 薄流动性
- Spread 扩大
- Spot 缺席
- Perp OI 暴涨
- Funding 快速过热
- 第一波后快速回吐
- 少量大单推动价格

高 Pump Risk 时，Opportunity 必须受惩罚（P18 调用）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clamp(v: float | None) -> float | None:
    if v is None:
        return None
    return max(0.0, min(100.0, v))


@dataclass
class PumpRiskResult:
    pump_risk_score: float | None
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"pump_risk_score": self.pump_risk_score, "label": self.label, "factors": self.factors}


class PumpRiskEngine:
    """异常拉升风险引擎。"""

    def __init__(
        self,
        extreme_return: float = 0.15,
        extreme_volume_z: float = 5.0,
        funding_hot: float = 0.0005,
        rapid_retrace: float = 0.5,
    ) -> None:
        self.extreme_return = extreme_return
        self.extreme_volume_z = extreme_volume_z
        self.funding_hot = funding_hot
        self.rapid_retrace = rapid_retrace

    def compute(self, fv: dict[str, Any]) -> PumpRiskResult:
        price_5m = fv.get("price_return_5m")
        vol_z = fv.get("volume_z")
        funding = fv.get("funding")
        retrace = fv.get("retrace_ratio")
        oi_5m = fv.get("oi_change_5m")
        spot_delta = fv.get("spot_delta")
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        eff = fv.get("price_efficiency")

        factors: dict[str, Any] = {}
        components: list[float] = []

        # 极端涨幅
        if price_5m is not None:
            ext_ret = _clamp(abs(price_5m) / self.extreme_return * 60.0) if abs(price_5m) > self.extreme_return * 0.5 else 0.0
            components.append(ext_ret)
            factors["extreme_return"] = ext_ret

        # 极端 volume
        if vol_z is not None:
            ext_vol = _clamp((vol_z - self.extreme_volume_z) * 15.0) if vol_z > self.extreme_volume_z else 0.0
            components.append(ext_vol)
            factors["extreme_volume"] = ext_vol

        # Funding 过热
        if funding is not None:
            funding_hot = _clamp(abs(funding) / self.funding_hot * 40.0) if abs(funding) > self.funding_hot * 0.5 else 0.0
            components.append(funding_hot)
            factors["funding_hot"] = funding_hot

        # 快速回吐
        if retrace is not None:
            rapid = _clamp((retrace - self.rapid_retrace) * 100.0) if retrace > self.rapid_retrace else 0.0
            components.append(rapid)
            factors["rapid_retrace"] = rapid

        # OI 暴涨
        if oi_5m is not None:
            oi_pump = _clamp(oi_5m / 0.1 * 40.0) if oi_5m > 0.05 else 0.0
            components.append(oi_pump)
            factors["oi_pump"] = oi_pump

        # Spot 缺席（合约单边拉升）
        if spot_delta is not None and delta is not None:
            spot_absent = 60.0 if (abs(delta) > 1000 and abs(spot_delta) < abs(delta) * 0.1) else 0.0
            components.append(spot_absent)
            factors["spot_absent"] = spot_absent

        # 少量大单（高 delta 低效率 = 少量大单推价）
        if delta is not None and eff is not None:
            few_large = 50.0 if (abs(delta) > 5000 and eff < 0.3) else 0.0
            components.append(few_large)
            factors["few_large_orders"] = few_large

        # 综合：max-weighted（任一极端信号即显著抬高，而非被平均稀释）
        if components:
            score = max(components) * 0.6 + (sum(components) / len(components)) * 0.4
        else:
            score = None

        if score is None:
            label = "数据不足"
        elif score > 60:
            label = "异常拉升风险高，Opportunity 受惩罚"
        elif score > 40:
            label = "有一定拉升风险"
        else:
            label = "暂无异常拉升风险"

        return PumpRiskResult(_clamp(score), label, factors)
