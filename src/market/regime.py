"""Market Regime Engine — 市场大环境判断（V1.2 §8）。

输入：BTC/ETH 多周期、universe 涨跌家数、异常放量比例、OI 扩张/收缩比例、波动率。
输出 regime：ALT_RISK_ON / ALT_RISK_OFF / BTC_DOMINANT / CHOP / DELEVERAGING / PANIC / NEUTRAL。

规则透明（无 ML），权重 uncalibrated，待 Replay Calibration 校准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.config import MarketRegimeConfig


@dataclass
class MarketSnapshot:
    """市场横截面快照。"""

    btc_return_5m: float | None = None
    btc_return_15m: float | None = None
    btc_return_1h: float | None = None
    eth_return_5m: float | None = None
    eth_return_15m: float | None = None
    eth_return_1h: float | None = None
    breadth_up: int = 0  # universe 上涨家数
    breadth_down: int = 0  # universe 下跌家数
    anomaly_ratio: float = 0.0  # 异常放量比例（候选 / universe）
    oi_expansion_ratio: float = 0.0  # OI 扩张 symbol 比例
    oi_contraction_ratio: float = 0.0  # OI 收缩 symbol 比例
    btc_volatility: float | None = None
    alt_volatility: float | None = None


REGIME_LABELS: dict[str, str] = {
    "ALT_RISK_ON": "山寨偏强",
    "ALT_RISK_OFF": "山寨偏弱",
    "BTC_DOMINANT": "BTC 主导",
    "CHOP": "市场震荡",
    "DELEVERAGING": "全市场去杠杆",
    "PANIC": "恐慌状态",
    "NEUTRAL": "中性",
}


@dataclass
class RegimeResult:
    """市场背景判定结果。"""

    regime: str
    label: str
    detail: str  # "BTC稳定 · 资金扩散中"
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "label": self.label,
            "detail": self.detail,
            "factors": self.factors,
        }


class MarketRegimeEngine:
    """市场背景引擎。"""

    def __init__(self, cfg: MarketRegimeConfig | None = None) -> None:
        self.cfg = cfg or MarketRegimeConfig()

    def compute(self, snap: MarketSnapshot) -> RegimeResult:
        c = self.cfg
        factors: dict[str, Any] = {}

        btc_1h = snap.btc_return_1h
        btc_5m = snap.btc_return_5m
        total_breadth = snap.breadth_up + snap.breadth_down
        up_ratio = snap.breadth_up / total_breadth if total_breadth > 0 else 0.5
        down_ratio = snap.breadth_down / total_breadth if total_breadth > 0 else 0.5

        # BTC 相对山寨强度（BTC 涨而山寨弱 → BTC 主导）
        alt_ret = snap.eth_return_1h
        btc_dominant = False
        if btc_1h is not None and alt_ret is not None:
            if btc_1h > c.btc_dominant_return and alt_ret < btc_1h - c.btc_dominant_gap:
                btc_dominant = True

        # 去杠杆 / 恐慌
        deleverage = False
        panic = False
        if btc_1h is not None:
            if btc_1h < -c.panic_return and down_ratio > c.panic_down_ratio:
                panic = True
            elif btc_1h < -c.deleverage_return and snap.oi_contraction_ratio > c.deleverage_oi_contract:
                deleverage = True

        # 山寨风险开关
        alt_risk_on = (
            up_ratio > c.risk_on_up_ratio
            and snap.anomaly_ratio > c.risk_on_anomaly_ratio
            and snap.oi_expansion_ratio > snap.oi_contraction_ratio
            and not panic
            and not deleverage
        )
        alt_risk_off = (
            down_ratio > c.risk_off_down_ratio
            and snap.oi_contraction_ratio > snap.oi_expansion_ratio
            and not panic
            and not deleverage
        )

        # 震荡
        chop = (
            snap.anomaly_ratio < c.chop_anomaly_ratio
            and abs(up_ratio - down_ratio) < c.chop_breadth_balance
            and not panic
            and not deleverage
            and not alt_risk_on
            and not alt_risk_off
            and not btc_dominant
        )

        # 优先级：PANIC > DELEVERAGING > BTC_DOMINANT > ALT_RISK_ON > ALT_RISK_OFF > CHOP > NEUTRAL
        if panic:
            regime = "PANIC"
        elif deleverage:
            regime = "DELEVERAGING"
        elif btc_dominant:
            regime = "BTC_DOMINANT"
        elif alt_risk_on:
            regime = "ALT_RISK_ON"
        elif alt_risk_off:
            regime = "ALT_RISK_OFF"
        elif chop:
            regime = "CHOP"
        else:
            regime = "NEUTRAL"

        factors = {
            "btc_return_1h": btc_1h,
            "up_ratio": round(up_ratio, 3),
            "down_ratio": round(down_ratio, 3),
            "anomaly_ratio": round(snap.anomaly_ratio, 3),
            "oi_expansion_ratio": round(snap.oi_expansion_ratio, 3),
            "oi_contraction_ratio": round(snap.oi_contraction_ratio, 3),
            "btc_dominant": btc_dominant,
            "panic": panic,
            "deleverage": deleverage,
            "alt_risk_on": alt_risk_on,
            "alt_risk_off": alt_risk_off,
            "chop": chop,
        }

        detail = self._detail(regime, snap, up_ratio, down_ratio)
        return RegimeResult(
            regime=regime,
            label=REGIME_LABELS.get(regime, regime),
            detail=detail,
            factors=factors,
        )

    def _detail(self, regime: str, snap: MarketSnapshot, up_ratio: float, down_ratio: float) -> str:
        btc_state = "BTC稳定"
        if snap.btc_return_1h is not None:
            if snap.btc_return_1h > 0.01:
                btc_state = "BTC走强"
            elif snap.btc_return_1h < -0.01:
                btc_state = "BTC走弱"
        flow = "资金扩散中" if snap.oi_expansion_ratio > snap.oi_contraction_ratio else "资金收缩中"
        if regime == "PANIC":
            return f"{btc_state} · {flow} · 市场恐慌"
        if regime == "DELEVERAGING":
            return f"{btc_state} · {flow} · 全市场去杠杆"
        if regime == "BTC_DOMINANT":
            return f"{btc_state} · 山寨资金流出"
        if regime == "ALT_RISK_ON":
            return f"{btc_state} · {flow}"
        if regime == "ALT_RISK_OFF":
            return f"{btc_state} · {flow}"
        if regime == "CHOP":
            return f"{btc_state} · 量能平淡"
        return f"{btc_state} · {flow}"
