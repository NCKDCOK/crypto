"""Volume Profile Engine — 成交密集区（V1.2 §17）。

基于公开成交（K 线 volume）构建 Volume Profile：
- POC (Point of Control)：成交量最大的价格档
- VAH / VAL (Value Area High/Low)：包含 70% 成交量的价格区间上下沿
- HVN / LVN (High/Low Volume Node)：高/低成交密集节点
- High Volume Zone / Low Volume Zone

用途：支撑/阻力、套牢区、Entry Zone、Invalidation、TP、Location Score。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domain import KlineEvent


@dataclass
class VolumeProfileResult:
    """Volume Profile 结果。"""
    poc: float | None = None  # Point of Control
    vah: float | None = None  # Value Area High
    val: float | None = None  # Value Area Low
    hvn: list[float] = field(default_factory=list)  # High Volume Nodes
    lvn: list[float] = field(default_factory=list)  # Low Volume Nodes
    high_volume_zones: list[tuple[float, float]] = field(default_factory=list)  # (low, high)
    low_volume_zones: list[tuple[float, float]] = field(default_factory=list)
    bins: list[dict[str, Any]] = field(default_factory=list)  # [{price_low, price_high, volume}]
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "poc": self.poc, "vah": self.vah, "val": self.val,
            "hvn": self.hvn, "lvn": self.lvn,
            "high_volume_zones": self.high_volume_zones,
            "low_volume_zones": self.low_volume_zones,
        }


class VolumeProfileEngine:
    """Volume Profile 引擎。"""

    def __init__(self, num_bins: int = 50, value_area_pct: float = 0.70,
                 hvn_threshold: float = 1.5, lvn_threshold: float = 0.5) -> None:
        self.num_bins = num_bins
        self.value_area_pct = value_area_pct
        self.hvn_threshold = hvn_threshold  # 相对均值的倍数
        self.lvn_threshold = lvn_threshold

    def compute(self, klines: list[KlineEvent]) -> VolumeProfileResult:
        result = VolumeProfileResult()
        if len(klines) < 3:
            return result

        # 价格范围
        all_highs = [float(k.high) for k in klines]
        all_lows = [float(k.low) for k in klines]
        price_min = min(all_lows)
        price_max = max(all_highs)
        if price_max <= price_min:
            return result

        # 分箱
        bin_size = (price_max - price_min) / self.num_bins
        bins_vol = [0.0] * self.num_bins
        # 每根 K 线的 volume 按价格范围均匀分配到覆盖的 bin
        for k in klines:
            kh = float(k.high)
            kl = float(k.low)
            vol = float(k.volume)
            lo_bin = max(0, int((kl - price_min) / bin_size))
            hi_bin = min(self.num_bins - 1, int((kh - price_min) / bin_size))
            if hi_bin < lo_bin:
                hi_bin = lo_bin
            spread = hi_bin - lo_bin + 1
            for b in range(lo_bin, hi_bin + 1):
                bins_vol[b] += vol / spread

        avg_vol = sum(bins_vol) / len(bins_vol) if bins_vol else 0.0

        # POC
        poc_bin = max(range(self.num_bins), key=lambda i: bins_vol[i])
        result.poc = price_min + (poc_bin + 0.5) * bin_size

        # Value Area（从 POC 向外扩展直到覆盖 value_area_pct）
        total_vol = sum(bins_vol)
        target = total_vol * self.value_area_pct
        va_vol = bins_vol[poc_bin]
        va_lo = poc_bin
        va_hi = poc_bin
        while va_vol < target and (va_lo > 0 or va_hi < self.num_bins - 1):
            expand_lo = bins_vol[va_lo - 1] if va_lo > 0 else -1
            expand_hi = bins_vol[va_hi + 1] if va_hi < self.num_bins - 1 else -1
            if expand_lo >= expand_hi and va_lo > 0:
                va_lo -= 1
                va_vol += bins_vol[va_lo]
            elif va_hi < self.num_bins - 1:
                va_hi += 1
                va_vol += bins_vol[va_hi]
            else:
                break
        result.vah = price_min + (va_hi + 1) * bin_size
        result.val = price_min + va_lo * bin_size

        # HVN / LVN
        hvn_bins = []
        lvn_bins = []
        for i, v in enumerate(bins_vol):
            price = price_min + (i + 0.5) * bin_size
            if avg_vol > 0 and v > avg_vol * self.hvn_threshold:
                result.hvn.append(round(price, 8))
                hvn_bins.append(i)
            elif avg_vol > 0 and v < avg_vol * self.lvn_threshold:
                result.lvn.append(round(price, 8))
                lvn_bins.append(i)

        # 连续 HVN/LVN 合并为 zones
        result.high_volume_zones = self._merge_zones(hvn_bins, price_min, bin_size)
        result.low_volume_zones = self._merge_zones(lvn_bins, price_min, bin_size)

        result.bins = [
            {"price_low": price_min + i * bin_size, "price_high": price_min + (i + 1) * bin_size, "volume": v}
            for i, v in enumerate(bins_vol)
        ]
        result.factors = {"num_bins": self.num_bins, "total_volume": total_vol, "avg_bin_volume": avg_vol}
        return result

    def _merge_zones(self, bin_indices: list[int], price_min: float, bin_size: float) -> list[tuple[float, float]]:
        if not bin_indices:
            return []
        zones = []
        start = bin_indices[0]
        prev = bin_indices[0]
        for b in bin_indices[1:]:
            if b == prev + 1:
                prev = b
            else:
                zones.append((price_min + start * bin_size, price_min + (prev + 1) * bin_size))
                start = b
                prev = b
        zones.append((price_min + start * bin_size, price_min + (prev + 1) * bin_size))
        return zones
