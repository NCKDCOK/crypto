"""Accumulation / Absorption Engine — 吸筹与承接识别（V1.2 §11）。

UI 字段：吸筹迹象（不是「已确认庄家吸筹」）。
证据：
- Sell Absorption：大量主动卖但价格不跌（承接）
- CVD/Price Divergence：CVD 与价格背离
- Low-Vol Turnover：低位换手
- OI Gradual Expansion：OI 渐增
- Spot Confirmation：现货确认
- Reclaim：收复关键位

权重 uncalibrated，待 Replay Calibration 校准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clamp(v: float | None, lo: float = 0.0, hi: float = 100.0) -> float | None:
    if v is None:
        return None
    return max(lo, min(hi, v))


@dataclass
class AccumulationResult:
    accumulation_score: float | None  # 0~100 综合吸筹迹象
    absorption_score: float | None  # 承接强度
    low_level_turnover_score: float | None  # 低位换手
    spot_accumulation_score: float | None  # 现货确认
    reclaim_score: float | None  # 收复
    oi_gradual_expansion: bool | None
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accumulation_score": self.accumulation_score,
            "absorption_score": self.absorption_score,
            "low_level_turnover_score": self.low_level_turnover_score,
            "spot_accumulation_score": self.spot_accumulation_score,
            "reclaim_score": self.reclaim_score,
            "oi_gradual_expansion": self.oi_gradual_expansion,
            "label": self.label,
            "factors": self.factors,
        }


class AccumulationEngine:
    """吸筹/承接引擎。"""

    def __init__(
        self,
        absorption_delta_threshold: float = 10000.0,
        absorption_eff_threshold: float = 0.3,
        oi_gradual_min: float = 0.0,
        oi_gradual_max: float = 0.05,
    ) -> None:
        self.absorption_delta_threshold = absorption_delta_threshold
        self.absorption_eff_threshold = absorption_eff_threshold
        self.oi_gradual_min = oi_gradual_min
        self.oi_gradual_max = oi_gradual_max

    def compute(self, fv: dict[str, Any], direction: str | None) -> AccumulationResult:
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        eff = fv.get("price_efficiency")
        retrace = fv.get("retrace_ratio")
        oi_5m = fv.get("oi_change_5m")
        cvd_z = fv.get("cvd_slope_z")
        acceptance = fv.get("acceptance")
        spot_delta = fv.get("spot_delta")
        price_5m = fv.get("price_return_5m")

        factors: dict[str, Any] = {}

        # 承接强度：主动卖但价格效率低（卖压被吃掉）
        absorption = None
        if delta is not None and eff is not None:
            # LONG 方向吸筹：卖盘（delta<0 或对 LONG 反向）但价格没跌
            sign = 1.0 if direction == "LONG" else (-1.0 if direction == "SHORT" else 0.0)
            sell_pressure = (delta * sign < -self.absorption_delta_threshold) if sign != 0 else (delta < -self.absorption_delta_threshold)
            if sell_pressure and eff < self.absorption_eff_threshold:
                absorption = 80.0 + (1.0 - eff) * 20.0
            elif sell_pressure:
                absorption = 50.0
            else:
                absorption = 20.0
        factors["absorption"] = absorption

        # CVD/价格背离：CVD 走弱但价格不跌/上涨
        cvd_div = None
        if cvd_z is not None and price_5m is not None:
            sign = 1.0 if direction == "LONG" else (-1.0 if direction == "SHORT" else 0.0)
            if sign != 0:
                diverged = (cvd_z * sign < 0) and (price_5m * sign >= 0)
                cvd_div = 75.0 if diverged else 25.0
        factors["cvd_divergence"] = cvd_div

        # 低位换手：回踩小 + 效率健康（在低位反复换手）
        turnover = None
        if retrace is not None and eff is not None:
            turnover = _clamp(100.0 - retrace * 100.0 + eff * 30.0)
        factors["low_level_turnover"] = turnover

        # OI 渐增（不是暴涨）
        oi_gradual = None
        if oi_5m is not None:
            oi_gradual = self.oi_gradual_min < oi_5m <= self.oi_gradual_max
        factors["oi_gradual_expansion"] = oi_gradual

        # 现货吸筹
        spot_accum = None
        if spot_delta is not None:
            sign = 1.0 if direction == "LONG" else (-1.0 if direction == "SHORT" else 0.0)
            spot_buy = (spot_delta * sign > 0) if sign != 0 else (spot_delta > 0)
            spot_accum = 75.0 if spot_buy else 25.0
        factors["spot_accumulation"] = spot_accum

        # 收复（acceptance 高 = 站稳/收复）
        reclaim = None
        if acceptance is not None:
            reclaim = _clamp(acceptance * 100.0)
        factors["reclaim"] = reclaim

        # 综合吸筹分（可用项加权平均，缺失移出分母 §5）
        components = [absorption, cvd_div, turnover, spot_accum, reclaim]
        avail = [c for c in components if c is not None]
        accum_score = (sum(avail) / len(avail)) if avail else None

        # 文案
        if accum_score is None:
            label = "数据不足，无法判断吸筹迹象"
        elif accum_score > 70:
            label = "出现明显吸筹/承接迹象"
        elif accum_score > 50:
            label = "有部分吸筹迹象，需更多确认"
        else:
            label = "暂无明显吸筹迹象"

        return AccumulationResult(
            accumulation_score=_clamp(accum_score),
            absorption_score=_clamp(absorption),
            low_level_turnover_score=_clamp(turnover),
            spot_accumulation_score=_clamp(spot_accum),
            reclaim_score=_clamp(reclaim),
            oi_gradual_expansion=oi_gradual,
            label=label,
            factors=factors,
        )
