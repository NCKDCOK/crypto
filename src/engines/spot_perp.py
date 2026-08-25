"""Spot × Perp Confirmation Engine — 现货与合约双市场确认（V1.2 §9）。

判定资金启动性质：
- 健康启动：Spot Buy↑ + Perp Buy↑ + OI↑ + Price↑（现货与合约资金同步进入）
- 杠杆主导：Spot weak + Perp Buy↑↑ + OI↑↑ + Funding↑ + Price↑（合约杠杆推动，持续性风险更高）

无现货数据时标记 unavailable，不伪造（§5）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpotPerpResult:
    """现货合约确认结果。"""

    spot_confirmed: bool  # 现货是否同步确认
    leverage_dominant: bool  # 是否杠杆主导
    classification: str  # healthy / leverage_dominant / spot_absent / unclear
    label: str  # 中文文案
    spot_perp_agreement: float | None = None
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spot_confirmed": self.spot_confirmed,
            "leverage_dominant": self.leverage_dominant,
            "classification": self.classification,
            "label": self.label,
            "spot_perp_agreement": self.spot_perp_agreement,
            "factors": self.factors,
        }


class SpotPerpConfirmationEngine:
    """现货×合约确认引擎。"""

    def __init__(
        self,
        spot_buy_threshold: float = 0.0,
        perp_buy_threshold: float = 0.0,
        oi_expansion_threshold: float = 0.0,
        funding_hot_threshold: float = 0.0002,
        spot_weak_ratio: float = 0.3,
    ) -> None:
        self.spot_buy_threshold = spot_buy_threshold
        self.perp_buy_threshold = perp_buy_threshold
        self.oi_expansion_threshold = oi_expansion_threshold
        self.funding_hot_threshold = funding_hot_threshold
        self.spot_weak_ratio = spot_weak_ratio

    def compute(self, fv: dict[str, Any], direction: str | None) -> SpotPerpResult:
        spot_buy = fv.get("spot_taker_buy")
        spot_sell = fv.get("spot_taker_sell")
        spot_delta = fv.get("spot_delta")
        perp_delta = fv.get("signed_delta") or fv.get("taker_delta")
        oi_5m = fv.get("oi_change_5m")
        funding = fv.get("funding")
        agreement = fv.get("spot_perp_agreement")

        spot_available = spot_delta is not None

        factors = {
            "spot_delta": spot_delta,
            "perp_delta": perp_delta,
            "oi_change_5m": oi_5m,
            "funding": funding,
            "spot_perp_agreement": agreement,
            "spot_available": spot_available,
        }

        if not spot_available:
            return SpotPerpResult(
                spot_confirmed=False, leverage_dominant=False,
                classification="spot_absent", label="无现货数据",
                spot_perp_agreement=agreement, factors=factors,
            )

        sign = 1.0 if direction == "LONG" else (-1.0 if direction == "SHORT" else 0.0)

        spot_aligned = (spot_delta * sign > self.spot_buy_threshold) if sign != 0 else (spot_delta > 0)
        perp_aligned = (perp_delta * sign > self.perp_buy_threshold) if (perp_delta is not None and sign != 0) else False
        oi_up = oi_5m is not None and oi_5m > self.oi_expansion_threshold
        funding_hot = funding is not None and abs(funding) > self.funding_hot_threshold

        # 现货相对合约强度
        spot_ratio = None
        if spot_buy is not None and perp_delta is not None and abs(perp_delta) > 0:
            spot_ratio = abs(spot_buy or 0) / (abs(perp_delta) + 1e-9)

        # 健康启动：现货+合约+OI 同向
        healthy = spot_aligned and perp_aligned and oi_up
        # 杠杆主导：合约强、现货弱、OI 大涨、Funding 偏热
        spot_weak = spot_ratio is not None and spot_ratio < self.spot_weak_ratio
        leverage = perp_aligned and spot_weak and oi_up and funding_hot

        if healthy and not leverage:
            classification = "healthy"
            label = "现货与合约资金同步进入"
        elif leverage:
            classification = "leverage_dominant"
            label = "本轮上涨主要由合约杠杆推动，持续性风险更高"
        elif spot_aligned and not perp_aligned:
            classification = "spot_only"
            label = "现货买入为主，合约资金尚未跟进"
        else:
            classification = "unclear"
            label = "现货与合约资金方向不一致"

        return SpotPerpResult(
            spot_confirmed=spot_aligned,
            leverage_dominant=leverage,
            classification=classification,
            label=label,
            spot_perp_agreement=agreement,
            factors=factors,
        )
