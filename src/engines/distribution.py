"""Distribution Engine — 派发风险（V1.2 §13）。

典型证据：
- 高位大量成交
- Taker Buy 很强但 Price 推不动
- CVD 继续涨
- Price Efficiency 下降
- OI 增速衰减
- 高位反复冲击失败
- Spot 卖压增强

输出 distribution_risk_score。用户解释：买盘仍大但上涨效率下降，上方可能存在持续卖压。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clamp(v: float | None) -> float | None:
    if v is None:
        return None
    return max(0.0, min(100.0, v))


@dataclass
class DistributionResult:
    distribution_risk_score: float | None  # 0~100
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"distribution_risk_score": self.distribution_risk_score, "label": self.label, "factors": self.factors}


class DistributionEngine:
    """派发风险引擎。"""

    def __init__(
        self,
        high_volume_z: float = 3.0,
        low_efficiency: float = 0.3,
        oi_decay_threshold: float = 0.0,
    ) -> None:
        self.high_volume_z = high_volume_z
        self.low_efficiency = low_efficiency
        self.oi_decay_threshold = oi_decay_threshold

    def compute(self, fv: dict[str, Any], direction: str | None) -> DistributionResult:
        vol_z = fv.get("volume_z")
        eff = fv.get("price_efficiency")
        cvd_z = fv.get("cvd_slope_z")
        oi_5m = fv.get("oi_change_5m")
        acceptance = fv.get("acceptance")
        spot_delta = fv.get("spot_delta")
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        price_5m = fv.get("price_return_5m")

        factors: dict[str, Any] = {}

        sign = 1.0 if direction == "LONG" else (-1.0 if direction == "SHORT" else 0.0)

        # 高量低效：放量但价格推不动
        high_vol_low_eff = None
        if vol_z is not None and eff is not None:
            if vol_z > self.high_volume_z and eff < self.low_efficiency:
                high_vol_low_eff = _clamp((vol_z - self.high_volume_z) * 20.0 + (self.low_efficiency - eff) * 100.0)
            else:
                high_vol_low_eff = 0.0
        factors["high_vol_low_eff"] = high_vol_low_eff

        # CVD 与价格背离（CVD 强但价格不涨）
        cvd_div = None
        if cvd_z is not None and price_5m is not None and sign != 0:
            if cvd_z * sign > 1.0 and price_5m * sign < 0.005:
                cvd_div = 70.0
            else:
                cvd_div = 10.0
        factors["cvd_divergence"] = cvd_div

        # OI 增速衰减
        oi_decay = None
        if oi_5m is not None:
            oi_decay = _clamp(abs(min(oi_5m, 0.0)) * 500.0) if oi_5m < self.oi_decay_threshold else 0.0
        factors["oi_decay"] = oi_decay

        # 突破失败（acceptance 低 = 反复冲击失败）
        failed_breakout = None
        if acceptance is not None:
            failed_breakout = _clamp((1.0 - acceptance) * 100.0)
        factors["failed_breakout"] = failed_breakout

        # 现货卖压
        spot_sell = None
        if spot_delta is not None and sign != 0:
            spot_sell = 70.0 if (spot_delta * sign < 0) else 10.0
        factors["spot_sell_pressure"] = spot_sell

        # 主动买强但价不动
        buy_no_progress = None
        if delta is not None and eff is not None and sign != 0:
            if delta * sign > 0 and eff < self.low_efficiency:
                buy_no_progress = 65.0
            else:
                buy_no_progress = 10.0
        factors["buy_no_progress"] = buy_no_progress

        components = [high_vol_low_eff, cvd_div, oi_decay, failed_breakout, spot_sell, buy_no_progress]
        avail = [c for c in components if c is not None]
        score = (sum(avail) / len(avail)) if avail else None

        if score is None:
            label = "数据不足，无法判断派发风险"
        elif score > 60:
            label = "买盘仍大但上涨效率明显下降，上方可能存在持续卖压"
        elif score > 40:
            label = "出现部分派发迹象，注意风险"
        else:
            label = "暂无明显派发风险"

        return DistributionResult(_clamp(score), label, factors)
