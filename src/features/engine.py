"""Feature Engine — 组装多窗口 FeatureSnapshot。

依据：ANALYSIS_MODEL.md, DATA_MODEL.md §6, 改造任务文档 §7/§11/§12
- 多时间尺度滚动窗口（5s/15s/30s/1m/5m trade flow + 1m/5m/15m/1h kline context）
- 统一 WindowManager，避免重复内存
- CVD 按每笔成交更新（每笔只计一次，修复旧编排层重叠窗口重复计数）
- FeatureSnapshot.data_health 由 runtime 注入；缺数据 → null/unavailable，fail closed
- 每个 feature 可追溯 provenance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from src.domain import (
    FeatureSnapshot,
    FeatureValue,
    FundingRateSnapshot,
    KlineEvent,
    OpenInterestSnapshot,
    TradeEvent,
)
from .efficiency_features import compute_efficiency_features
from .flow_features import CVDTracker, compute_taker_buy_sell_volume, compute_taker_delta
from .oi_features import compute_oi_features
from .price_features import compute_price_features, compute_price_return
from .volume_features import compute_volume_features
from .context_features import compute_context_features
from .baseline import compute_baseline

logger = logging.getLogger(__name__)

# 窗口标签 ↔ 毫秒
WINDOW_LABELS: dict[str, int] = {
    "5s": 5_000,
    "15s": 15_000,
    "30s": 30_000,
    "1m": 60_000,
    "5m": 300_000,
}
WINDOW_BY_MS: dict[int, str] = {v: k for k, v in WINDOW_LABELS.items()}

PRIMARY_WINDOW_MS = 30_000  # z-score / efficiency 主窗口


@dataclass
class FeatureEngineState:
    """Feature Engine 每个 symbol 的状态。"""

    symbol: str
    window_manager: object  # WindowManager
    cvd: CVDTracker = field(default_factory=CVDTracker)
    # 基线历史（按主窗口）
    baseline_volumes: list[float] = field(default_factory=list)
    baseline_trade_counts: list[float] = field(default_factory=list)
    baseline_quote_volumes: list[float] = field(default_factory=list)
    # OI 快照历史
    oi_snapshots: list[OpenInterestSnapshot] = field(default_factory=list)
    # Funding 基线
    funding_baseline: list[FundingRateSnapshot] = field(default_factory=list)
    latest_funding: FundingRateSnapshot | None = None
    # Kline 上下文：interval → 最近 closed bar
    klines: dict[str, KlineEvent] = field(default_factory=dict)
    # 健康摘要：stream → HealthLevel value
    health_summary: dict[str, str] = field(default_factory=dict)
    # 最新 FeatureSnapshot
    last_snapshot: FeatureSnapshot | None = None
    last_receive_time: int | None = None


class FeatureEngine:
    """特征引擎。

    接收标准化事件（trade/oi/funding/kline），经多窗口计算特征，输出 FeatureSnapshot。
    CVD 按每笔成交更新（每笔只计一次）。每个特征可追溯到 provenance。
    """

    def __init__(
        self,
        trade_flow_windows_ms: Sequence[int] = (5_000, 15_000, 30_000, 60_000, 300_000),
        kline_intervals: Sequence[str] = ("1m", "5m", "15m", "1h"),
        epsilon: float = 1.0,
        oi_tolerance_ms: int = 15_000,
        baseline_max_samples: int = 360,
    ) -> None:
        self.trade_flow_windows_ms = tuple(trade_flow_windows_ms)
        self.kline_intervals = tuple(kline_intervals)
        self.epsilon = epsilon
        self.oi_tolerance_ms = oi_tolerance_ms
        self.baseline_max_samples = baseline_max_samples
        self._states: dict[str, FeatureEngineState] = {}

    def get_state(self, symbol: str) -> FeatureEngineState:
        if symbol not in self._states:
            from src.windows.rolling_window import WindowManager
            wm = WindowManager(list(self.trade_flow_windows_ms))
            self._states[symbol] = FeatureEngineState(symbol=symbol, window_manager=wm)
        return self._states[symbol]

    # ── 事件注入 ──

    def add_trade(self, symbol: str, trade: TradeEvent) -> None:
        """注入一笔成交 → WindowManager + CVD（每笔只计一次）。"""
        state = self.get_state(symbol)
        state.window_manager.add(trade.receive_time, trade)
        # CVD 按每笔成交更新
        signed = float(trade.quote_notional)
        if trade.aggressor_side.value == "SELL":
            signed = -signed
        elif trade.aggressor_side.value == "UNKNOWN":
            signed = 0.0
        state.cvd.update(symbol, signed, trade.receive_time)
        state.last_receive_time = trade.receive_time

    def add_oi_snapshot(self, snap: OpenInterestSnapshot) -> None:
        state = self.get_state(snap.symbol)
        state.oi_snapshots.append(snap)
        # 保留最近 5m + 容差的快照（足够算 30s/1m/5m/15m）
        cutoff = snap.receive_time - 1_000_000
        state.oi_snapshots = [s for s in state.oi_snapshots if s.receive_time >= cutoff]

    def add_funding_snapshot(self, snap: FundingRateSnapshot) -> None:
        state = self.get_state(snap.symbol)
        state.funding_baseline.append(snap)
        state.latest_funding = snap
        if len(state.funding_baseline) > self.baseline_max_samples:
            state.funding_baseline = state.funding_baseline[-self.baseline_max_samples:]

    def add_kline(self, kline: KlineEvent) -> None:
        state = self.get_state(kline.symbol)
        # 仅保留最近 closed bar（慢周期确认）；未闭合也更新以供实时 context
        state.klines[kline.interval.value] = kline

    def set_health(self, symbol: str, health_summary: dict[str, str]) -> None:
        """注入该 symbol 各流的健康摘要（stream → HealthLevel value）。"""
        state = self.get_state(symbol)
        state.health_summary = dict(health_summary)

    def clear(self, symbol: str | None = None) -> None:
        if symbol:
            self._states.pop(symbol, None)
        else:
            self._states.clear()

    # ── 计算 ──

    def compute_snapshot(self, symbol: str, now_ms: int) -> FeatureSnapshot:
        """计算当前时刻的多窗口 FeatureSnapshot。"""
        state = self.get_state(symbol)
        wm = state.window_manager
        features: dict[str, FeatureValue] = {}
        provenance: dict = {}

        primary_trades = wm.get_items(PRIMARY_WINDOW_MS, now_ms)
        direction_hint = self._infer_direction_hint(state, primary_trades)

        # ── 多窗口 raw 特征 ──
        for w_ms in self.trade_flow_windows_ms:
            label = WINDOW_BY_MS.get(w_ms, str(w_ms))
            w_trades = wm.get_items(w_ms, now_ms)
            pret = _safe(compute_price_return(w_trades))
            vol = _window_volume(w_trades)
            tc = len(w_trades)
            delta = compute_taker_delta(w_trades)
            buy_v, sell_v = compute_taker_buy_sell_volume(w_trades)
            features[f"price_return_{label}"] = _fv(pret, label)
            features[f"volume_{label}"] = _fv(vol, label)
            features[f"trade_count_{label}"] = _fv(float(tc) if tc else None, label)
            features[f"taker_delta_{label}"] = _fv(delta, label)
            features[f"taker_buy_volume_{label}"] = _fv(buy_v, label)
            features[f"taker_sell_volume_{label}"] = _fv(sell_v, label)

        # ── 量类 z-score / rvol / 加速度（主窗口 30s）──
        vol_feats = compute_volume_features(
            primary_trades,
            state.baseline_volumes,
            state.baseline_trade_counts,
        )
        features["relative_volume"] = _fv(vol_feats.rvol, "30s")
        features["volume_zscore"] = _fv(vol_feats.volume_z, "30s")
        features["trade_count_zscore"] = _fv(vol_feats.trade_count_z, "30s")
        features["volume_acceleration"] = _fv(vol_feats.volume_acceleration, "30s")
        features["average_trade_size"] = _fv(vol_feats.average_trade_size, "30s")
        features["large_trade_ratio"] = _fv(vol_feats.large_trade_ratio, "30s")
        # legacy 别名（detector 读取）
        features["rvol"] = _fv(vol_feats.rvol, "30s")
        features["volume_z"] = _fv(vol_feats.volume_z, "30s")
        features["trade_count_z"] = _fv(vol_feats.trade_count_z, "30s")
        provenance["volume"] = {
            "trade_count": vol_feats.window_trade_count,
            "window_volume": vol_feats.window_volume,
            "source_streams": ["aggTrade"],
        }

        # ── 资金流（主窗口 30s + CVD 全程）──
        delta = compute_taker_delta(primary_trades)
        buy_v, sell_v = compute_taker_buy_sell_volume(primary_trades)
        delta_ratio = None
        if buy_v is not None and sell_v is not None and (buy_v + sell_v) > 0:
            delta_ratio = (buy_v - sell_v) / (buy_v + sell_v)
        cvd = state.cvd.get_cvd(symbol)
        cvd_slope = state.cvd.get_cvd_slope(symbol)
        cvd_slope_z = state.cvd.get_cvd_slope_z(symbol)
        cvd_accel_z = state.cvd.get_cvd_accel_z(symbol)
        features["signed_delta"] = _fv(delta, "30s")
        features["taker_buy_volume"] = _fv(buy_v, "30s")
        features["taker_sell_volume"] = _fv(sell_v, "30s")
        features["delta_ratio"] = _fv(delta_ratio, "30s")
        features["cvd"] = _fv(cvd, None)
        features["CVD_slope"] = _fv(cvd_slope, "30s")
        features["cvd_slope_z"] = _fv(cvd_slope_z, "30s")
        features["cvd_accel_z"] = _fv(cvd_accel_z, "30s")
        # legacy 别名（detector 读取）
        features["taker_delta"] = _fv(delta, "30s")
        provenance["flow"] = {"cvd": cvd, "source_streams": ["aggTrade"]}

        # ── 价类（主窗口 + 5m 参考突破）──
        ref_long = wm.get_items(300_000, now_ms)
        price_feats = compute_price_features(primary_trades, ref_long, direction_hint)
        features["price_acceleration"] = _fv(price_feats.price_acceleration, "30s")
        features["high_break"] = _fv(
            1.0 if price_feats.high_break else (0.0 if price_feats.high_break is False else None), None
        )
        features["low_break"] = _fv(
            1.0 if price_feats.low_break else (0.0 if price_feats.low_break is False else None), None
        )
        features["acceptance"] = _fv(price_feats.acceptance, "30s")
        provenance["price"] = {"source_streams": ["aggTrade"]}

        # ── 效率（主窗口）──
        baseline_quote = compute_baseline(state.baseline_quote_volumes)
        eff_feats = compute_efficiency_features(
            primary_trades, self.epsilon,
            baseline_notional=baseline_quote.median if baseline_quote.median > 0 else None,
        )
        features["directional_efficiency"] = _fv(eff_feats.directional_efficiency, "30s")
        features["flow_impact"] = _fv(eff_feats.flow_impact, "30s")
        features["retrace_ratio"] = _fv(eff_feats.retrace_ratio, "30s")
        features["price_efficiency"] = _fv(eff_feats.price_efficiency, "30s")
        provenance["efficiency"] = {
            "trade_count": len(primary_trades),
            "source_streams": ["aggTrade"],
        }

        # ── OI ──
        oi_feats = compute_oi_features(state.oi_snapshots, self.oi_tolerance_ms)
        features["oi_contracts"] = _fv(oi_feats.oi_contracts, None)
        features["oi_change_30s"] = _fv(oi_feats.oi_change_30s, "30s")
        features["oi_change_1m"] = _fv(oi_feats.oi_change_1m, "1m")
        features["oi_change_5m"] = _fv(oi_feats.oi_change_5m, "5m")
        features["oi_velocity"] = _fv(oi_feats.oi_velocity, None)
        features["oi_acceleration"] = _fv(oi_feats.oi_accel, None)
        provenance["oi"] = {
            "snapshot_count": len(state.oi_snapshots),
            "source_streams": ["oi_poller"],
        }

        # ── Context: funding / premium / kline ──
        if state.latest_funding is not None:
            ctx = compute_context_features(state.latest_funding, state.funding_baseline)
            features["funding"] = _fv(float(state.latest_funding.last_funding_rate), None)
            features["premium"] = _fv(float(state.latest_funding.premium), None)
            features["funding_percentile"] = _fv(ctx.funding_percentile, None)
            features["premium_percentile"] = _fv(ctx.premium_percentile, None)
            provenance["context"] = {"source_streams": ["funding_premium"]}
        # kline 上下文：各周期最近 closed bar 的 return
        for interval in self.kline_intervals:
            kl = state.klines.get(interval)
            if kl is not None and kl.is_closed:
                o = float(kl.open)
                c = float(kl.close)
                ret = (c - o) / o if o != 0 else None
                features[f"context_{interval}"] = _fv(ret, interval)
            else:
                features[f"context_{interval}"] = _fv(None, interval)
        provenance["kline"] = {"source_streams": ["kline"]}

        # ── Quality ──
        features["source_age"] = _fv(
            (now_ms - state.last_receive_time) if state.last_receive_time else None, None
        )
        any_stale = any(v in ("STALE", "DRIFT", "FAIL") for v in state.health_summary.values())
        features["stale_flag"] = _fv(1.0 if any_stale else 0.0, None)
        provenance["quality"] = {
            "health": dict(state.health_summary),
            "source_streams": list(state.health_summary.keys()) or ["aggTrade"],
        }

        snap = FeatureSnapshot(
            symbol=symbol,
            asof=now_ms,
            windows={WINDOW_BY_MS.get(w, str(w)): WINDOW_BY_MS.get(w, str(w))
                     for w in self.trade_flow_windows_ms},
            data_health=dict(state.health_summary),
            features=features,
            provenance=provenance,
        )
        state.last_snapshot = snap

        # 更新主窗口基线
        if primary_trades:
            self._update_baseline(state, vol_feats.window_volume, vol_feats.window_trade_count)

        return snap

    def _infer_direction_hint(self, state: FeatureEngineState, trades: Sequence[TradeEvent]) -> str | None:
        delta = compute_taker_delta(trades)
        if delta is None:
            return None
        if delta > 0:
            return "LONG"
        if delta < 0:
            return "SHORT"
        return None

    def _update_baseline(self, state: FeatureEngineState, window_volume: float, trade_count: int) -> None:
        state.baseline_volumes.append(window_volume)
        state.baseline_trade_counts.append(float(trade_count))
        state.baseline_quote_volumes.append(window_volume)  # 简化：用成交量做 quote 基线归一参考
        max_s = self.baseline_max_samples
        if len(state.baseline_volumes) > max_s:
            state.baseline_volumes = state.baseline_volumes[-max_s:]
            state.baseline_trade_counts = state.baseline_trade_counts[-max_s:]
            state.baseline_quote_volumes = state.baseline_quote_volumes[-max_s:]


# ── helpers ──

def _fv(value: float | None, window: str | None) -> FeatureValue:
    return FeatureValue(value=value, available=value is not None, window=window)


def _window_volume(trades: Sequence[TradeEvent]) -> float | None:
    if not trades:
        return None
    return float(sum(float(t.qty) for t in trades))


def _safe(v: float | None) -> float | None:
    return v
