"""Structure Engine — 价格结构识别（V1.2 §16）。

V1.2 第一版实现：
- Swing High / Swing Low
- HH / HL / LH / LL 序列
- Local High / Low
- Support / Resistance
- Breakout Level / Retest Zone
- Failed Breakout / Failed Breakdown
- VWAP
- ATR

基于 closed K 线序列（FeatureEngine.kline_history，由持久化/恢复加载）。
不要求复杂 SMC 全家桶。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.domain import KlineEvent


@dataclass
class SwingPoint:
    """摆动点。"""
    price: float
    time: int
    kind: str  # high / low


@dataclass
class StructureResult:
    """价格结构结果。"""
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)
    structure_sequence: list[str] = field(default_factory=list)  # HH/HL/LH/LL
    local_high: float | None = None
    local_low: float | None = None
    resistance: float | None = None
    support: float | None = None
    breakout_level: float | None = None
    retest_zone_low: float | None = None
    retest_zone_high: float | None = None
    failed_breakout: bool = False
    failed_breakdown: bool = False
    vwap: float | None = None
    atr: float | None = None
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "swing_highs": [{"price": s.price, "time": s.time, "kind": s.kind} for s in self.swing_highs[-5:]],
            "swing_lows": [{"price": s.price, "time": s.time, "kind": s.kind} for s in self.swing_lows[-5:]],
            "structure_sequence": self.structure_sequence[-6:],
            "local_high": self.local_high,
            "local_low": self.local_low,
            "resistance": self.resistance,
            "support": self.support,
            "breakout_level": self.breakout_level,
            "retest_zone": [self.retest_zone_low, self.retest_zone_high] if self.retest_zone_low else None,
            "failed_breakout": self.failed_breakout,
            "failed_breakdown": self.failed_breakdown,
            "vwap": self.vwap,
            "atr": self.atr,
        }


class StructureEngine:
    """价格结构引擎。"""

    def __init__(self, swing_window: int = 3, atr_period: int = 14) -> None:
        self.swing_window = swing_window
        self.atr_period = atr_period

    def compute(self, klines: list[KlineEvent], *, current_price: float | None = None) -> StructureResult:
        """从 closed K 线序列计算结构。"""
        result = StructureResult()
        if len(klines) < self.swing_window * 2 + 1:
            # 数据不足，仅计算 VWAP/ATR 如可
            if klines:
                result.vwap = self._vwap(klines[-self.atr_period:])
                result.atr = self._atr(klines[-self.atr_period:])
            return result

        # Swing High/Low（fractal：window 内最高/最低）
        highs = self._find_swings(klines, "high")
        lows = self._find_swings(klines, "low")
        result.swing_highs = highs
        result.swing_lows = lows

        # HH/HL/LH/LL 序列
        result.structure_sequence = self._structure_sequence(highs, lows)

        # Local High/Low（最近 N 根）
        recent = klines[-max(self.atr_period, 10):]
        result.local_high = max(float(k.high) for k in recent)
        result.local_low = min(float(k.low) for k in recent)

        # Resistance / Support（最近 swing）
        if highs:
            result.resistance = highs[-1].price
        if lows:
            result.support = lows[-1].price

        # Breakout Level（最近阻力位）+ Retest Zone（阻力±ATR 的一定比例）
        result.atr = self._atr(klines[-self.atr_period:])
        if highs and result.atr:
            result.breakout_level = highs[-1].price
            band = result.atr * 0.5
            result.retest_zone_low = highs[-1].price - band
            result.retest_zone_high = highs[-1].price + band

        # VWAP
        result.vwap = self._vwap(klines[-self.atr_period:])

        # Failed Breakout / Breakdown
        if current_price is not None and highs and lows and result.atr:
            # 曾突破阻力但回落到阻力下方
            if current_price < highs[-1].price and any(
                float(k.high) > highs[-1].price for k in klines[-5:]
            ):
                result.failed_breakout = True
            # 曾跌破支撑但回升到支撑上方
            if current_price > lows[-1].price and any(
                float(k.low) < lows[-1].price for k in klines[-5:]
            ):
                result.failed_breakdown = True

        result.factors = {
            "kline_count": len(klines),
            "swing_count": len(highs) + len(lows),
            "has_atr": result.atr is not None,
        }
        return result

    def _find_swings(self, klines: list[KlineEvent], kind: str) -> list[SwingPoint]:
        """fractal swing：某根 K 线高/低是前后 window 根中最极端的。"""
        w = self.swing_window
        points: list[SwingPoint] = []
        for i in range(w, len(klines) - w):
            if kind == "high":
                val = float(klines[i].high)
                is_swing = all(float(klines[i].high) >= float(klines[j].high) for j in range(i - w, i + w + 1) if j != i)
            else:
                val = float(klines[i].low)
                is_swing = all(float(klines[i].low) <= float(klines[j].low) for j in range(i - w, i + w + 1) if j != i)
            if is_swing:
                points.append(SwingPoint(price=val, time=klines[i].open_time, kind=kind))
        return points

    def _structure_sequence(self, highs: list[SwingPoint], lows: list[SwingPoint]) -> list[str]:
        """从 swing 序列推导 HH/HL/LH/LL。"""
        seq: list[str] = []
        # 交替比较 highs 和 lows
        all_points = sorted(highs + lows, key=lambda p: p.time)
        prev_high = None
        prev_low = None
        for p in all_points:
            if p.kind == "high":
                if prev_high is not None:
                    seq.append("HH" if p.price > prev_high else "LH")
                prev_high = p.price
            else:
                if prev_low is not None:
                    seq.append("HL" if p.price > prev_low else "LL")
                prev_low = p.price
        return seq

    def _vwap(self, klines: list[KlineEvent]) -> float | None:
        if not klines:
            return None
        total_pv = 0.0
        total_v = 0.0
        for k in klines:
            typical = (float(k.high) + float(k.low) + float(k.close)) / 3.0
            vol = float(k.volume)
            total_pv += typical * vol
            total_v += vol
        return total_pv / total_v if total_v > 0 else None

    def _atr(self, klines: list[KlineEvent]) -> float | None:
        if len(klines) < 2:
            return None
        trs = []
        for i in range(1, len(klines)):
            h = float(klines[i].high)
            l = float(klines[i].low)
            prev_c = float(klines[i - 1].close)
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        period = min(self.atr_period, len(trs))
        return sum(trs[-period:]) / period if trs else None
