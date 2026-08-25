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
from .impulse_features import compute_impulse_asymmetry
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
    # V1.2 §9 Spot 数据
    spot_window_manager: object | None = None  # WindowManager for spot trades
    spot_cvd: CVDTracker = field(default_factory=CVDTracker)
    spot_available: bool = False  # 该 symbol 是否有现货市场
    spot_last_receive_time: int | None = None
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
    # Kline 历史序列：interval → closed bar 列表（V1.2 Structure/VP 用，恢复时加载）
    kline_history: dict[str, list[KlineEvent]] = field(default_factory=dict)
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
        kline_history_max: int = 500,
    ) -> None:
        self.trade_flow_windows_ms = tuple(trade_flow_windows_ms)
        self.kline_intervals = tuple(kline_intervals)
        self.epsilon = epsilon
        self.oi_tolerance_ms = oi_tolerance_ms
        self.baseline_max_samples = baseline_max_samples
        self.kline_history_max = kline_history_max
        self._states: dict[str, FeatureEngineState] = {}

    def get_state(self, symbol: str) -> FeatureEngineState:
        if symbol not in self._states:
            from src.windows.rolling_window import WindowManager
            wm = WindowManager(list(self.trade_flow_windows_ms))
            self._states[symbol] = FeatureEngineState(
                symbol=symbol, window_manager=wm,
                spot_window_manager=WindowManager(list(self.trade_flow_windows_ms)),
            )
        return self._states[symbol]

    def set_spot_available(self, symbol: str, available: bool) -> None:
        """标记该 symbol 是否有现货市场（runtime 由 SpotSymbolRegistry 注入）。"""
        state = self.get_state(symbol)
        state.spot_available = available

    def add_spot_trade(self, symbol: str, trade: TradeEvent) -> None:
        """注入一笔现货成交 → spot WindowManager + spot CVD。"""
        state = self.get_state(symbol)
        if state.spot_window_manager is None:
            from src.windows.rolling_window import WindowManager
            state.spot_window_manager = WindowManager(list(self.trade_flow_windows_ms))
        state.spot_window_manager.add(trade.receive_time, trade)
        signed = float(trade.quote_notional)
        if trade.aggressor_side.value == "SELL":
            signed = -signed
        elif trade.aggressor_side.value == "UNKNOWN":
            signed = 0.0
        state.spot_cvd.update(symbol, signed, trade.receive_time)
        state.spot_last_receive_time = trade.receive_time

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
        # V1.2：closed bar 追加历史序列（供 Structure/VP）
        if kline.is_closed:
            hist = state.kline_history.setdefault(kline.interval.value, [])
            # 去重 + 保持时间正序
            if not hist or kline.open_time > hist[-1].open_time:
                hist.append(kline)
            elif kline.open_time < hist[0].open_time:
                hist.insert(0, kline)
            # 上限保护
            if len(hist) > self.kline_history_max:
                state.kline_history[kline.interval.value] = hist[-self.kline_history_max:]

    def load_kline_history(self, symbol: str, interval: str, bars: list[KlineEvent]) -> None:
        """从持久化加载历史 K 线序列（恢复时调用）。"""
        state = self.get_state(symbol)
        sorted_bars = sorted(bars, key=lambda k: k.open_time)
        state.kline_history[interval] = sorted_bars[-self.kline_history_max:]
        if sorted_bars:
            state.klines[interval] = sorted_bars[-1]

    def get_kline_history(self, symbol: str, interval: str) -> list[KlineEvent]:
        state = self.get_state(symbol)
        return list(state.kline_history.get(interval, []))

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

        # ── V1.2 §9 Spot 数据（现货市场资金流）──
        if state.spot_available and state.spot_window_manager is not None:
            spot_trades = state.spot_window_manager.get_items(PRIMARY_WINDOW_MS, now_ms)
            spot_vol = _window_volume(spot_trades) or 0.0
            spot_buy, spot_sell = compute_taker_buy_sell_volume(spot_trades)
            spot_delta = compute_taker_delta(spot_trades)
            spot_cvd = state.spot_cvd.get_cvd(symbol)
            spot_cvd_slope = state.spot_cvd.get_cvd_slope(symbol)
            spot_cvd_slope_z = state.spot_cvd.get_cvd_slope_z(symbol)
            features["spot_volume"] = _fv(spot_vol, "30s")
            features["spot_taker_buy"] = _fv(spot_buy, "30s")
            features["spot_taker_sell"] = _fv(spot_sell, "30s")
            features["spot_delta"] = _fv(spot_delta, "30s")
            features["spot_cvd"] = _fv(spot_cvd, None)
            features["spot_cvd_slope"] = _fv(spot_cvd_slope, "30s")
            features["spot_cvd_slope_z"] = _fv(spot_cvd_slope_z, "30s")
            # Spot × Perp 一致性（P6）：现货 delta 与合约 delta 同向 → 1，反向 → -1
            if spot_delta is not None and delta is not None:
                agreement = 1.0 if (spot_delta * delta > 0) else (-1.0 if (spot_delta * delta < 0) else 0.0)
            else:
                agreement = None
            features["spot_perp_agreement"] = _fv(agreement, "30s")
            provenance["spot"] = {"source_streams": ["spot_aggTrade"]}
        else:
            # 无现货市场 → 标记 unavailable（§5：不伪造）
            for k in ("spot_volume", "spot_taker_buy", "spot_taker_sell", "spot_delta",
                      "spot_cvd", "spot_cvd_slope", "spot_cvd_slope_z", "spot_perp_agreement"):
                features[k] = _fv(None, None)

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

        # ── V1.2 §10 多空推动效率（Impulse Asymmetry）──
        impulse = compute_impulse_asymmetry(primary_trades)
        features["upside_velocity"] = _fv(impulse.upside_velocity, "30s")
        features["downside_velocity"] = _fv(impulse.downside_velocity, "30s")
        features["upside_volume_efficiency"] = _fv(impulse.upside_volume_efficiency, "30s")
        features["downside_volume_efficiency"] = _fv(impulse.downside_volume_efficiency, "30s")
        features["upside_delta_efficiency"] = _fv(impulse.upside_delta_efficiency, "30s")
        features["downside_delta_efficiency"] = _fv(impulse.downside_delta_efficiency, "30s")
        features["impulse_ratio"] = _fv(impulse.impulse_ratio, "30s")

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
