"""Feature Engine — 组装 FeatureSnapshot。

依据：ANALYSIS_MODEL.md, DATA_MODEL.md §6
把原始行情经滚动窗口计算为特征，输出 FeatureSnapshot（含 provenance）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from src.domain import (
    FeatureSnapshot,
    FeatureValue,
    FundingRateSnapshot,
    OpenInterestSnapshot,
    TradeEvent,
)
from .efficiency_features import compute_efficiency_features
from .flow_features import CVDTracker, compute_flow_features
from .oi_features import compute_oi_features
from .volume_features import compute_volume_features

logger = logging.getLogger(__name__)


@dataclass
class FeatureEngineState:
    """Feature Engine 每个 symbol 的状态。"""

    symbol: str
    # 基线历史
    baseline_volumes: list[float] = field(default_factory=list)
    baseline_trade_counts: list[float] = field(default_factory=list)
    # OI 快照历史
    oi_snapshots: list[OpenInterestSnapshot] = field(default_factory=list)
    # Funding 基线
    funding_baseline: list[FundingRateSnapshot] = field(default_factory=list)
    # 最新 FeatureSnapshot
    last_snapshot: FeatureSnapshot | None = None


class FeatureEngine:
    """特征引擎。

    接收标准化事件，经滚动窗口计算特征，输出 FeatureSnapshot。
    每个特征可追溯到 provenance。
    """

    def __init__(
        self,
        epsilon: float = 1.0,
        oi_tolerance_ms: int = 15_000,
    ) -> None:
        self.epsilon = epsilon
        self.oi_tolerance_ms = oi_tolerance_ms
        self._states: dict[str, FeatureEngineState] = {}
        self._cvd = CVDTracker()

    def get_state(self, symbol: str) -> FeatureEngineState:
        if symbol not in self._states:
            self._states[symbol] = FeatureEngineState(symbol=symbol)
        return self._states[symbol]

    def update_baseline(
        self,
        symbol: str,
        window_volume: float,
        window_trade_count: int,
    ) -> None:
        """更新基线历史。"""
        state = self.get_state(symbol)
        state.baseline_volumes.append(window_volume)
        state.baseline_trade_counts.append(float(window_trade_count))

    def add_oi_snapshot(self, snap: OpenInterestSnapshot) -> None:
        state = self.get_state(snap.symbol)
        state.oi_snapshots.append(snap)

    def add_funding_snapshot(self, snap: FundingRateSnapshot) -> None:
        state = self.get_state(snap.symbol)
        state.funding_baseline.append(snap)

    def compute_snapshot(
        self,
        symbol: str,
        window_trades: Sequence[TradeEvent],
        now_ms: int,
        window_label: str = "30s",
    ) -> FeatureSnapshot:
        """计算当前时刻的 FeatureSnapshot。

        Args:
            symbol: 交易对
            window_trades: 当前窗口内的 TradeEvent 列表
            now_ms: 当前时间
            window_label: 窗口标签

        Returns:
            FeatureSnapshot 含全部可用特征 + provenance
        """
        state = self.get_state(symbol)

        features: dict[str, FeatureValue] = {}
        provenance: dict = {}

        # ── 量类特征 ──
        vol_feats = compute_volume_features(
            window_trades,
            state.baseline_volumes,
            state.baseline_trade_counts,
        )
        features["rvol"] = FeatureValue(
            value=vol_feats.rvol,
            available=vol_feats.rvol is not None,
            window=window_label,
        )
        features["volume_z"] = FeatureValue(
            value=vol_feats.volume_z,
            available=vol_feats.volume_z is not None,
            window=window_label,
        )
        features["trade_count_z"] = FeatureValue(
            value=vol_feats.trade_count_z,
            available=vol_feats.trade_count_z is not None,
            window=window_label,
        )
        provenance["volume"] = {
            "trade_count": len(window_trades),
            "window_volume": vol_feats.window_volume,
            "source_streams": ["aggTrade"],
        }

        # ── 资金流特征 ──
        flow_feats = compute_flow_features(symbol, window_trades, self._cvd, now_ms)
        features["taker_delta"] = FeatureValue(
            value=flow_feats.taker_delta,
            available=flow_feats.taker_delta is not None,
            window=window_label,
        )
        features["cvd"] = FeatureValue(
            value=flow_feats.cvd,
            available=True,
        )
        features["cvd_slope_z"] = FeatureValue(
            value=flow_feats.cvd_slope_z,
            available=flow_feats.cvd_slope_z is not None,
            window=window_label,
        )
        features["cvd_accel_z"] = FeatureValue(
            value=flow_feats.cvd_accel_z,
            available=flow_feats.cvd_accel_z is not None,
            window=window_label,
        )
        provenance["flow"] = {
            "cvd": flow_feats.cvd,
            "source_streams": ["aggTrade"],
        }

        # ── OI 特征 ──
        oi_feats = compute_oi_features(state.oi_snapshots, self.oi_tolerance_ms)
        features["oi_change_1m"] = FeatureValue(
            value=oi_feats.oi_change_1m,
            available=oi_feats.oi_change_1m is not None,
            window="1m",
        )
        features["oi_change_5m"] = FeatureValue(
            value=oi_feats.oi_change_5m,
            available=oi_feats.oi_change_5m is not None,
            window="5m",
        )
        features["oi_velocity"] = FeatureValue(
            value=oi_feats.oi_velocity,
            available=oi_feats.oi_velocity is not None,
        )
        features["oi_accel"] = FeatureValue(
            value=oi_feats.oi_accel,
            available=oi_feats.oi_accel is not None,
        )
        provenance["oi"] = {
            "snapshot_count": len(state.oi_snapshots),
            "source_streams": ["oi_poller"],
        }

        # ── 效率特征 ──
        eff_feats = compute_efficiency_features(window_trades, self.epsilon)
        features["directional_efficiency"] = FeatureValue(
            value=eff_feats.directional_efficiency,
            available=eff_feats.directional_efficiency is not None,
            window=window_label,
        )
        features["flow_impact"] = FeatureValue(
            value=eff_feats.flow_impact,
            available=eff_feats.flow_impact is not None,
            window=window_label,
        )
        features["retrace_ratio"] = FeatureValue(
            value=eff_feats.retrace_ratio,
            available=eff_feats.retrace_ratio is not None,
        )
        provenance["efficiency"] = {
            "trade_count": len(window_trades),
            "source_streams": ["aggTrade"],
        }

        snap = FeatureSnapshot(
            symbol=symbol,
            asof=now_ms,
            windows={window_label: window_label},
            features=features,
            provenance=provenance,
        )
        state.last_snapshot = snap

        # 更新基线
        if window_trades:
            self.update_baseline(
                symbol,
                vol_feats.window_volume,
                vol_feats.window_trade_count,
            )

        return snap
