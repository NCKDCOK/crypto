"""Setup Type Engine — Setup 分类（V1.2 §14）。

主生命周期（State）保持不变，新增 Setup Type 独立分类：
ACCUMULATION / BREAKOUT_START / RETEST_REIGNITION / TREND_CONTINUATION /
SHORT_SQUEEZE / LONG_LIQUIDATION / OVERSOLD_REBOUND / DISTRIBUTION /
PUMP_RISK / NONE

依据：state + direction + 行为引擎结果 + 价格结构特征。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domain import State


SETUP_LABELS: dict[str, str] = {
    "ACCUMULATION": "疑似吸筹",
    "BREAKOUT_START": "突破启动",
    "RETEST_REIGNITION": "回踩复燃",
    "TREND_CONTINUATION": "趋势延续",
    "SHORT_SQUEEZE": "逼空",
    "LONG_LIQUIDATION": "多头踩踏",
    "OVERSOLD_REBOUND": "超跌反抽",
    "DISTRIBUTION": "派发迹象",
    "PUMP_RISK": "异常拉升风险",
    "NONE": "无明确 Setup",
}


@dataclass
class SetupTypeResult:
    setup_type: str
    label: str
    confidence: str  # high / medium / low
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_type": self.setup_type, "label": self.label,
            "confidence": self.confidence, "factors": self.factors,
        }


class SetupTypeEngine:
    """Setup 类型判定引擎。"""

    def __init__(
        self,
        pump_return_threshold: float = 0.15,
        oversold_threshold: float = -0.08,
        breakout_acceptance: float = 0.5,
    ) -> None:
        self.pump_return_threshold = pump_return_threshold
        self.oversold_threshold = oversold_threshold
        self.breakout_acceptance = breakout_acceptance

    def compute(
        self,
        state: State,
        direction: str | None,
        fv: dict[str, Any],
        *,
        accumulation_score: float | None = None,
        distribution_risk: float | None = None,
        revival_score: float | None = None,
        leverage_dominant: bool = False,
        spot_confirmed: bool = False,
    ) -> SetupTypeResult:
        price_5m = fv.get("price_return_5m")
        oi_5m = fv.get("oi_change_5m")
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        acceptance = fv.get("acceptance")
        cvd_z = fv.get("cvd_slope_z")

        factors: dict[str, Any] = {
            "state": state.value, "direction": direction,
            "price_return_5m": price_5m, "oi_change_5m": oi_5m,
            "accumulation_score": accumulation_score,
            "distribution_risk": distribution_risk,
        }

        sign = 1.0 if direction == "LONG" else (-1.0 if direction == "SHORT" else 0.0)

        # Pump 风险：短时间极端涨幅
        if price_5m is not None and abs(price_5m) > self.pump_return_threshold:
            return SetupTypeResult("PUMP_RISK", SETUP_LABELS["PUMP_RISK"], "high", factors)

        # 派发：distribution_risk 高
        if distribution_risk is not None and distribution_risk > 65:
            return SetupTypeResult("DISTRIBUTION", SETUP_LABELS["DISTRIBUTION"], "medium", factors)

        # 逼空 / 多头踩踏：Delta 与方向同向 + OI↓（仓位被平，非新增）
        if sign != 0 and oi_5m is not None and oi_5m < 0 and delta is not None and delta * sign > 0:
            if direction == "LONG":
                return SetupTypeResult("SHORT_SQUEEZE", SETUP_LABELS["SHORT_SQUEEZE"], "medium", factors)
            return SetupTypeResult("LONG_LIQUIDATION", SETUP_LABELS["LONG_LIQUIDATION"], "medium", factors)

        # 超跌反抽：大跌后反弹
        if price_5m is not None and price_5m < self.oversold_threshold and sign > 0:
            return SetupTypeResult("OVERSOLD_REBOUND", SETUP_LABELS["OVERSOLD_REBOUND"], "low", factors)

        # 吸筹：accumulation 高 + 处于早期状态
        if accumulation_score is not None and accumulation_score > 65 and state in (
            State.SLEEPING, State.ANOMALY, State.SUSPECTED_START,
        ):
            return SetupTypeResult("ACCUMULATION", SETUP_LABELS["ACCUMULATION"], "medium", factors)

        # 突破启动：START_CONFIRMED + acceptance 高
        if state == State.START_CONFIRMED and acceptance is not None and acceptance > self.breakout_acceptance:
            return SetupTypeResult("BREAKOUT_START", SETUP_LABELS["BREAKOUT_START"], "high", factors)

        # 回踩复燃：CONTINUATION + retrace 健康（回踩后重新走强）
        if state == State.CONTINUATION and fv.get("retrace_ratio") is not None:
            retrace = fv.get("retrace_ratio")
            if retrace is not None and retrace < 0.4 and cvd_z is not None and cvd_z * sign > 0:
                return SetupTypeResult("RETEST_REIGNITION", SETUP_LABELS["RETEST_REIGNITION"], "medium", factors)
            return SetupTypeResult("TREND_CONTINUATION", SETUP_LABELS["TREND_CONTINUATION"], "medium", factors)

        # CONTINUATION 默认趋势延续
        if state == State.CONTINUATION:
            return SetupTypeResult("TREND_CONTINUATION", SETUP_LABELS["TREND_CONTINUATION"], "medium", factors)

        # 沉睡复活：revival 高 + SLEEPING/ANOMALY
        if revival_score is not None and revival_score > 60 and state in (State.SLEEPING, State.ANOMALY):
            return SetupTypeResult("ACCUMULATION", "疑似吸筹·沉睡复活", "low", factors)

        return SetupTypeResult("NONE", SETUP_LABELS["NONE"], "low", factors)
