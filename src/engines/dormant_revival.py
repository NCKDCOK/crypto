"""Dormant Revival Engine — 沉睡复活（V1.2 §12）。

识别：低成交/低波动/低 OI 变化之后出现 成交增速↑/TradeCount↑/SpotFlow↑/OI↑/Price 尚未严重拉升。
只用于提前进入观察列表，不直接触发买入结论。

近似：volume_z 上升 + oi 转正 + 价格尚未大涨（price_return_5m 小）→ 复活迹象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clamp(v: float | None) -> float | None:
    if v is None:
        return None
    return max(0.0, min(100.0, v))


@dataclass
class DormantRevivalResult:
    revival_score: float | None  # 0~100
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"revival_score": self.revival_score, "label": self.label, "factors": self.factors}


class DormantRevivalEngine:
    """沉睡复活引擎。"""

    def __init__(
        self,
        volume_z_threshold: float = 2.0,
        price_not_pumped: float = 0.03,
        oi_turn_up: float = 0.0,
    ) -> None:
        self.volume_z_threshold = volume_z_threshold
        self.price_not_pumped = price_not_pumped
        self.oi_turn_up = oi_turn_up

    def compute(self, fv: dict[str, Any]) -> DormantRevivalResult:
        vol_z = fv.get("volume_z")
        tc_z = fv.get("trade_count_z")
        oi_5m = fv.get("oi_change_5m")
        price_5m = fv.get("price_return_5m")
        spot_delta = fv.get("spot_delta")

        factors = {
            "volume_z": vol_z, "trade_count_z": tc_z, "oi_change_5m": oi_5m,
            "price_return_5m": price_5m, "spot_delta": spot_delta,
        }

        # 成交活跃信号
        vol_active = vol_z is not None and vol_z > self.volume_z_threshold
        tc_active = tc_z is not None and tc_z > self.volume_z_threshold
        # OI 转正
        oi_up = oi_5m is not None and oi_5m > self.oi_turn_up
        # 价格尚未大涨
        not_pumped = price_5m is None or abs(price_5m) < self.price_not_pumped
        # 现货流入
        spot_in = spot_delta is not None and spot_delta > 0

        signals = sum([vol_active, tc_active, oi_up, spot_in])
        # 复活分：活跃信号数 × 25，但需价格未大涨才成立
        if not_pumped and signals >= 2:
            score = float(signals) * 25.0
            label = "沉睡后资金开始活跃，价格尚未拉升，进入观察"
        elif not_pumped and signals == 1:
            score = 25.0
            label = "成交略有活跃，复活迹象偏弱"
        elif not not_pumped:
            score = 15.0
            label = "价格已明显拉升，复活阶段可能已过"
        else:
            score = 0.0
            label = "暂无复活迹象"

        return DormantRevivalResult(_clamp(score), label, factors)
