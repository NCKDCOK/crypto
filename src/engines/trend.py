"""Trend Engine V1.2 — 趋势强度（V1.2 §20）。

不要只用最后一根 K 线 open→close。至少基于：
- HH / HL / LH / LL 序列
- multi-bar slope
- ATR-normalized return
- VWAP relation
- breakout structure
- 1m / 5m / 15m / 1h agreement

用户显示：趋势强度：88
Drawer：5m：多头 / 15m：多头 / 1h：中性偏多
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domain import KlineEvent


def _clamp(v: float | None) -> float | None:
    if v is None:
        return None
    return max(0.0, min(100.0, v))


@dataclass
class TrendResult:
    trend_score: float | None  # 0~100
    direction: str  # up / down / neutral
    per_tf: dict[str, str]  # {1m: up, 5m: up, ...}
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trend_score": self.trend_score,
            "direction": self.direction,
            "per_tf": self.per_tf,
            "label": self.label,
            "factors": self.factors,
        }


class TrendEngine:
    """趋势引擎 V1.2。"""

    def __init__(self, slope_lookback: int = 10, atr_period: int = 14) -> None:
        self.slope_lookback = slope_lookback
        self.atr_period = atr_period

    def compute(
        self,
        fv: dict[str, Any],
        klines: list[KlineEvent] | None = None,
        structure=None,
    ) -> TrendResult:
        # 多周期 context（return）
        per_tf: dict[str, str] = {}
        tf_returns = {}
        for iv in ("1m", "5m", "15m", "1h"):
            val = fv.get(f"context_{iv}")
            if val is not None:
                tf_returns[iv] = val
                if val > 0.001:
                    per_tf[iv] = "多头"
                elif val < -0.001:
                    per_tf[iv] = "空头"
                else:
                    per_tf[iv] = "中性"

        # 多周期一致性
        aligned_up = sum(1 for v in tf_returns.values() if v > 0)
        aligned_down = sum(1 for v in tf_returns.values() if v < 0)
        total_tf = len(tf_returns)

        # multi-bar slope（from klines）
        slope_score = None
        if klines and len(klines) >= self.slope_lookback:
            recent = klines[-self.slope_lookback:]
            closes = [float(k.close) for k in recent]
            if len(closes) >= 2 and closes[0] != 0:
                slope = (closes[-1] - closes[0]) / closes[0]
                slope_score = _clamp(50.0 + slope * 2000.0)

        # ATR-normalized return
        atr_norm = None
        if klines and len(klines) >= 2:
            trs = []
            for i in range(1, min(len(klines), self.atr_period + 1)):
                h = float(klines[i].high)
                l = float(klines[i].low)
                pc = float(klines[i - 1].close)
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            atr = sum(trs) / len(trs) if trs else 0.0
            if atr > 0 and klines:
                ret = (float(klines[-1].close) - float(klines[0].close)) / float(klines[0].close)
                atr_norm = _clamp(50.0 + (ret / atr) * 30.0)

        # VWAP relation
        vwap_score = None
        if structure and structure.vwap is not None and klines:
            current = float(klines[-1].close)
            if current > structure.vwap:
                vwap_score = 70.0
            else:
                vwap_score = 30.0

        # breakout structure
        breakout_score = None
        if structure and structure.structure_sequence:
            seq = structure.structure_sequence[-3:]
            hh_count = seq.count("HH") + seq.count("HL")
            ll_count = seq.count("LL") + seq.count("LH")
            if hh_count > ll_count:
                breakout_score = 75.0
            elif ll_count > hh_count:
                breakout_score = 25.0
            else:
                breakout_score = 50.0

        # 综合
        components = [c for c in [slope_score, atr_norm, vwap_score, breakout_score] if c is not None]
        if total_tf > 0:
            tf_score = (aligned_up - aligned_down) / total_tf * 50.0 + 50.0
            components.append(tf_score)

        trend_score = (sum(components) / len(components)) if components else None

        # 方向
        if trend_score is not None:
            if trend_score > 60:
                direction = "up"
                label = "多头趋势"
            elif trend_score < 40:
                direction = "down"
                label = "空头趋势"
            else:
                direction = "neutral"
                label = "趋势中性"
        else:
            direction = "neutral"
            label = "数据不足"

        factors = {
            "slope_score": slope_score, "atr_normalized": atr_norm,
            "vwap_score": vwap_score, "breakout_score": breakout_score,
            "tf_alignment_up": aligned_up, "tf_alignment_down": aligned_down,
        }
        return TrendResult(_clamp(trend_score), direction, per_tf, label, factors)
