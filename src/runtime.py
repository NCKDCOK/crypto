"""Market Radar Runtime — 实盘编排层。

依据：改造任务文档 §4-§10, §25-§26, ARCHITECTURE.md §3
串联：Binance → Collectors → Health → WindowManager → FeatureEngine → Detectors
       → StateMachine → latest_state/last_transition → Dashboard/Alerts/Repository

两阶段 Radar：
- Stage1 LightScanner：低成本 REST 扫描全 universe → ANOMALY_CANDIDATE
- Stage2 DeepScanner：候选 symbol 跑 aggTrade WS + Kline WS + OI + Funding
  + 完整 Feature/Detector/StateMachine

关键不变量：
- confidence 由 FreshnessWatchdog + ConfidenceTracker 真实派生（无演示覆盖）
- latest_state（CurrentState）与 last_transition（TransitionEvent）分离
- stale 时 Fail Closed（confidence UNKNOWN → 禁止 CONFIRMED）
- bounded queue + QueueLagMonitor
- 所有 REST 经统一 RateLimiter
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.clock import Clock, SystemClock
from src.collectors.aggtrade_collector import AggTradeCollector
from src.collectors.base_ws import WSStreamConfig
from src.collectors.funding_collector import FundingPremiumCollector
from src.collectors.kline_collector import KlineCollector
from src.collectors.oi_poller import OIPoller
from src.collectors.symbol_registry import SymbolRegistry
from src.config import AppConfigBundle
from src.domain import (
    AnalysisEvent,
    ConfidenceState,
    Direction,
    FeatureSnapshot,
    HealthLevel,
    HealthStatus,
    KlineInterval,
    State,
)
from src.features.engine import FeatureEngine, WINDOW_BY_MS
from src.health.confidence import ConfidenceTracker
from src.health.freshness_watchdog import (
    FreshnessBudget,
    FreshnessWatchdog,
    StreamType,
)
from src.health.oi_lookup import OILookup
from src.health.queue_lag_monitor import QueueLagMonitor
from src.health.rate_limiter import RateLimiter, RateLimiterConfig
from src.alerts.manager import AlertManager
from src.state_machine.machine import StateMachine
from src.scoring.engine import ScoreEngine
from src.scoring.confidence import ConfidenceEngine
from src.presentation.translator import PresentationTranslator
from src.presentation.ranking import rank_symbols, generate_system_conclusion

logger = logging.getLogger(__name__)

STREAM_AGGTRADE = "aggTrade"
STREAM_KLINE = "kline"
STREAM_OI = "oi_poller"
STREAM_FUNDING = "funding_premium"


# ────────────────────────────────────────────────────────────────────
# 工厂
# ────────────────────────────────────────────────────────────────────


def build_feature_engine(cfg: AppConfigBundle) -> FeatureEngine:
    windows_ms = [WINDOW_BY_MS[w] for w in cfg.features.trade_flow_windows if w in WINDOW_BY_MS]
    if not windows_ms:
        windows_ms = [5_000, 15_000, 30_000, 60_000, 300_000]
    return FeatureEngine(
        trade_flow_windows_ms=windows_ms,
        kline_intervals=cfg.features.kline_context_intervals,
        epsilon=cfg.features.epsilon,
        oi_tolerance_ms=cfg.data_health.freshness.oi_lookup_tolerance_ms,
        baseline_max_samples=cfg.features.baseline_max_samples,
    )


def build_state_machine(cfg: AppConfigBundle) -> StateMachine:
    d = cfg.detectors
    sm = cfg.state_machine
    from src.detectors.anomaly import AnomalyDetector
    from src.detectors.continuation_withdrawal import (
        ContinuationDetector,
        ExhaustionDetector,
        WithdrawalDetector,
    )
    from src.detectors.false_start import FalseStartFilter
    from src.detectors.startup import StartupDetector

    return StateMachine(
        anomaly_detector=AnomalyDetector(
            volume_z_threshold=d.anomaly_volume_z,
            trade_count_z_threshold=d.anomaly_trade_count_z,
            price_accel_z_threshold=d.anomaly_price_accel_z,
            taker_delta_z_threshold=d.anomaly_taker_delta_z,
        ),
        startup_detector=StartupDetector(
            confirmation_hold_s=_parse_seconds(sm.confirmation_hold),
            oi_expansion_threshold=d.startup_oi_expansion_threshold,
            min_efficiency=d.startup_min_efficiency,
            max_retrace=d.startup_max_retrace,
            min_evidence=d.startup_min_evidence,
        ),
        false_start_filter=FalseStartFilter(
            rapid_retrace_threshold=d.veto_rapid_retrace_threshold,
            absorption_flow_impact_threshold=d.veto_absorption_flow_impact_threshold,
            absorption_delta_threshold=d.veto_absorption_delta_threshold,
            crowding_percentile_threshold=d.veto_crowding_percentile_threshold,
            one_bar_spike_retrace=d.veto_one_bar_spike_retrace,
        ),
        continuation_detector=ContinuationDetector(min_oi_maintain=d.continuation_min_oi_maintain),
        exhaustion_detector=ExhaustionDetector(min_divergence_count=d.exhaustion_min_divergence_count),
        withdrawal_detector=WithdrawalDetector(min_evidence_count=d.withdrawal_min_evidence_count),
        confidence_tracker=ConfidenceTracker(),
        anomaly_decay_s=_parse_seconds(sm.anomaly_decay_window),
        cooldown_s=float(sm.cooldown_seconds),
    )


def _parse_seconds(s: str) -> float:
    s = s.strip()
    if s.endswith("ms"):
        return float(s[:-2]) / 1000.0
    if s.endswith("s"):
        return float(s[:-1])
    if s.endswith("m"):
        return float(s[:-1]) * 60.0
    if s.endswith("h"):
        return float(s[:-1]) * 3600.0
    return float(s)


# ────────────────────────────────────────────────────────────────────
# 运行时状态
# ────────────────────────────────────────────────────────────────────


@dataclass
class SymbolRuntimeState:
    """CurrentState — 当前 symbol 状态（每次 compute 更新）。"""

    symbol: str
    state: State = State.SLEEPING
    direction: Direction | None = None
    confidence_state: ConfidenceState = ConfidenceState.UNKNOWN
    features: dict[str, Any] = field(default_factory=dict)
    health: dict[str, str] = field(default_factory=dict)
    evidence_count: int = 0
    veto_count: int = 0
    last_transition_at: int | None = None
    state_since_ms: int | None = None
    last_update_ms: int | None = None
    # light scanner 信息
    light_score: float = 0.0
    price_change_24h: float = 0.0
    quote_volume_24h: float = 0.0
    # 数据计数
    trade_count: int = 0
    dup_count: int = 0
    # 评分（V1.1）
    opportunity_score: float = 0.0
    score_available: bool = False
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    confidence_available: bool = False
    confidence_breakdown: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    stale_flag: float = 0.0


# ────────────────────────────────────────────────────────────────────
# 动态 Universe
# ────────────────────────────────────────────────────────────────────


class SymbolUniverse:
    """USDT-M 永续 dynamic universe。

    从 exchangeInfo 发现 + 24h ticker 按 quote volume 排序，应用 liquidity floor /
    blacklist / whitelist / top_n / max_symbols。
    """

    def __init__(self, cfg: AppConfigBundle, rate_limiter: RateLimiter, clock: Clock) -> None:
        self.cfg = cfg
        self.registry = SymbolRegistry(
            quote_asset=cfg.symbols.quote_asset,
            exclude_patterns=cfg.symbols.exclude_patterns,
        )
        self.rate_limiter = rate_limiter
        self.clock = clock
        self.base_url = cfg.app.rest_base_url
        # symbol → 24h ticker 摘要
        self.tickers: dict[str, dict[str, float]] = {}
        self.universe: list[str] = []

    async def refresh(self) -> list[str]:
        """刷新 universe：exchangeInfo + 24h ticker。"""
        # exchangeInfo（不走 RateLimiter 权重，但经统一 client）
        client = await self.rate_limiter._get_client()
        # exchangeInfo
        try:
            r = await client.get(f"{self.base_url}/fapi/v1/exchangeInfo", timeout=15)
            if r.status_code == 200:
                self.registry.update_from_exchange_info(r.json())
        except Exception as e:
            logger.warning("universe_exchangeinfo_error %s", e)
        # 24h ticker（全市场，单请求）
        try:
            r = await client.get(f"{self.base_url}/fapi/v1/ticker/24hr", timeout=20)
            if r.status_code == 200:
                self._update_tickers(r.json())
        except Exception as e:
            logger.warning("universe_ticker_error %s", e)

        self.universe = self._rank_and_filter()
        logger.info("universe_refreshed size=%d", len(self.universe))
        return self.universe

    def _update_tickers(self, data: list[dict]) -> None:
        self.tickers = {}
        for d in data:
            sym = d.get("symbol", "")
            try:
                self.tickers[sym] = {
                    "quote_volume": float(d.get("quoteVolume", 0)),
                    "volume": float(d.get("volume", 0)),
                    "price_change_pct": float(d.get("priceChangePercent", 0)),
                    "count": float(d.get("count", 0)),
                    "last_price": float(d.get("lastPrice", 0)),
                }
            except (ValueError, TypeError):
                continue

    def _rank_and_filter(self) -> list[str]:
        s = self.cfg.symbols
        valid = set(self.registry.get_all_symbols())
        # whitelist 优先
        if s.whitelist:
            candidates = [sym for sym in s.whitelist if sym in valid]
        else:
            candidates = list(valid)
        # liquidity floor
        candidates = [
            sym for sym in candidates
            if self.tickers.get(sym, {}).get("quote_volume", 0) >= s.liquidity_floor_usdt
        ]
        # 按 24h quote volume 排序
        candidates.sort(
            key=lambda sym: self.tickers.get(sym, {}).get("quote_volume", 0),
            reverse=True,
        )
        top = candidates[: s.top_n]
        if len(top) > s.max_symbols:
            top = top[: s.max_symbols]
        return top

    def get_ticker(self, symbol: str) -> dict[str, float]:
        return self.tickers.get(symbol, {})


# ────────────────────────────────────────────────────────────────────
# Stage1 LightScanner
# ────────────────────────────────────────────────────────────────────


class LightScanner:
    """Stage1 — 低成本扫描 universe，找"突然不正常"的 symbol。

    P0.1+P0.2 修复：
    - 不再使用 24h 累计值本身做异常
    - 计算相邻采样之间的短时增量（ΔQuoteVolume / ΔTradeCount / ΔPricePct）
    - 对增量维护 rolling baseline，计算 delta z-score
    - 只有至少 N 个增量信号超阈值才成为 ANOMALY_CANDIDATE
    - 禁止 score>0 这种"非零即候选"的逻辑
    - 输出 ANOMALY_CANDIDATE，不直接 LONG/SHORT
    """

    def __init__(self, cfg: AppConfigBundle, universe: SymbolUniverse, clock: Clock) -> None:
        self.cfg = cfg
        self.universe = universe
        self.clock = clock
        self.d = cfg.detectors
        # symbol → 上一次采样的 (quote_volume, count, last_price)
        self._prev: dict[str, dict[str, float]] = {}
        # symbol → rolling baseline of deltas
        self._delta_baseline: dict[str, dict[str, list[float]]] = {}
        self._baseline_max = cfg.features.baseline_max_samples

    def scan(self) -> list[tuple[str, float]]:
        """扫描 universe，返回 (symbol, score) 候选列表（降序）。

        只有至少 min_anomaly_signals 个增量 z-score 超阈值才入选。
        """
        from src.features.baseline import compute_baseline, robust_z_score

        scored: list[tuple[str, float]] = []
        now_ms = self.clock.now_ms()

        for sym in self.universe.universe:
            tk = self.universe.get_ticker(sym)
            qv = tk.get("quote_volume", 0.0)
            cnt = tk.get("count", 0.0)
            price = tk.get("last_price", 0.0)

            prev = self._prev.get(sym)
            bl = self._delta_baseline.setdefault(sym, {"dqv": [], "dcnt": [], "dprice": []})

            if prev is not None:
                # 计算短时增量
                dqv = qv - prev.get("quote_volume", 0.0)
                dcnt = cnt - prev.get("count", 0.0)
                prev_price = prev.get("last_price", 0.0)
                dprice_pct = ((price - prev_price) / prev_price * 100.0) if prev_price > 0 else 0.0

                # 计算 delta z-score
                dqv_bl = compute_baseline(bl["dqv"])
                dcnt_bl = compute_baseline(bl["dcnt"])
                dprice_bl = compute_baseline(bl["dprice"])

                dqv_z = robust_z_score(dqv, dqv_bl)
                dcnt_z = robust_z_score(dcnt, dcnt_bl)
                dprice_z = robust_z_score(dprice_pct, dprice_bl)

                # 统计超阈值信号数
                signals = 0
                score = 0.0

                if dqv_z is not None and abs(dqv_z) > self.d.light_volume_delta_z:
                    signals += 1
                    score += abs(dqv_z)
                if dcnt_z is not None and abs(dcnt_z) > self.d.light_trade_count_delta_z:
                    signals += 1
                    score += abs(dcnt_z) * 0.5
                if dprice_z is not None and abs(dprice_z) > self.d.light_price_delta_z:
                    signals += 1
                    score += abs(dprice_z) * 0.3

                # 只有足够多信号才成候选（禁止 score>0 即入选）
                if signals >= self.d.light_min_anomaly_signals:
                    scored.append((sym, score))

                # 更新基线
                bl["dqv"].append(dqv)
                bl["dcnt"].append(dcnt)
                bl["dprice"].append(dprice_pct)
                if len(bl["dqv"]) > self._baseline_max:
                    bl["dqv"] = bl["dqv"][-self._baseline_max:]
                    bl["dcnt"] = bl["dcnt"][-self._baseline_max:]
                    bl["dprice"] = bl["dprice"][-self._baseline_max:]

            # 保存当前采样作为下次的 prev
            self._prev[sym] = {"quote_volume": qv, "count": cnt, "last_price": price}

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


# ────────────────────────────────────────────────────────────────────
# DeepScanner（Stage2）
# ────────────────────────────────────────────────────────────────────


class DeepScanner:
    """Stage2 — 候选 symbol 的深度资金行为分析。

    管理 aggTrade WS + Kline WS（多周期）+ OI poller + Funding poller。
    P0.3：订阅 1m/5m/15m/1h 全部周期。
    P0.4：集合变化时增量 subscribe/unsubscribe，不整组重连。
    """

    def __init__(
        self,
        cfg: AppConfigBundle,
        runtime: "MarketRadarRuntime",
    ) -> None:
        self.cfg = cfg
        self.runtime = runtime
        self.symbols: list[str] = []
        self._aggtrade: AggTradeCollector | None = None
        self._kline: KlineCollector | None = None
        self._oi: OIPoller | None = None
        self._funding: FundingPremiumCollector | None = None
        self._trade_q: asyncio.Queue | None = None
        self._consumer_task: asyncio.Task | None = None
        self._running = False
        # 多周期 Kline intervals
        self._kline_intervals = [
            KlineInterval(i) for i in cfg.features.kline_context_intervals
        ]

    async def set_symbols(self, symbols: list[str]) -> None:
        """更新深度分析 symbol 集。

        P0.4：集合变化时增量 subscribe/unsubscribe，不整组重连。
        首次启动（无 collector）时全量启动。
        """
        new_set = set(symbols)
        old_set = set(self.symbols)

        if new_set == old_set:
            return

        if not new_set:
            # 清空 — 停止所有 collectors
            await self._stop_collectors()
            self.symbols = []
            return

        if not self._running:
            # 首次启动 — 全量
            self.symbols = list(symbols)
            if self.symbols:
                await self._start_collectors()
            return

        # 增量变更
        added = new_set - old_set
        removed = old_set - new_set

        if added:
            await self._add_symbols(list(added))
        if removed:
            await self._remove_symbols(list(removed))

        self.symbols = list(symbols)

    async def _add_symbols(self, syms: list[str]) -> None:
        """增量添加 symbol — WS subscribe + REST add + watchdog register。"""
        for sym in syms:
            self.runtime.watchdog.register_stream(
                f"{STREAM_AGGTRADE}:{sym}", sym, StreamType.AGGTRADE)
            self.runtime.watchdog.register_stream(
                f"{STREAM_KLINE}:{sym}", sym, StreamType.KLINE)
            self.runtime.watchdog.register_stream(
                f"{STREAM_OI}:{sym}", sym, StreamType.OI_POLLER)
            self.runtime.watchdog.register_stream(
                f"{STREAM_FUNDING}:{sym}", sym, StreamType.FUNDING_PREMIUM)

        # WS 增量订阅
        if self._aggtrade:
            await self._aggtrade.subscribe(AggTradeCollector.build_streams(syms))
        if self._kline:
            await self._kline.subscribe(
                KlineCollector.build_streams(syms, self._kline_intervals))

        # REST 增量添加
        if self._oi:
            for sym in syms:
                self._oi.add_symbol(sym)
        if self._funding:
            for sym in syms:
                self._funding.add_symbol(sym)

        logger.info("deep_scanner_added symbols=%s total=%d", syms, len(self.symbols) + len(syms))

    async def _remove_symbols(self, syms: list[str]) -> None:
        """增量移除 symbol — WS unsubscribe + REST remove + watchdog unregister。"""
        # WS 增量退订
        if self._aggtrade:
            await self._aggtrade.unsubscribe(AggTradeCollector.build_streams(syms))
        if self._kline:
            await self._kline.unsubscribe(
                KlineCollector.build_streams(syms, self._kline_intervals))

        # REST 增量移除
        if self._oi:
            for sym in syms:
                self._oi.remove_symbol(sym)
        if self._funding:
            for sym in syms:
                self._funding.remove_symbol(sym)

        # watchdog 注销
        for sym in syms:
            self.runtime.watchdog.unregister_stream(f"{STREAM_AGGTRADE}:{sym}")
            self.runtime.watchdog.unregister_stream(f"{STREAM_KLINE}:{sym}")
            self.runtime.watchdog.unregister_stream(f"{STREAM_OI}:{sym}")
            self.runtime.watchdog.unregister_stream(f"{STREAM_FUNDING}:{sym}")

        logger.info("deep_scanner_removed symbols=%s total=%d", syms, len(self.symbols) - len(syms))

    async def _start_collectors(self) -> None:
        proxy = self.cfg.app.proxy
        ws_base = self.cfg.app.ws_base_url
        rest_base = self.cfg.app.rest_base_url

        # 注册 health 流
        for sym in self.symbols:
            self.runtime.watchdog.register_stream(
                f"{STREAM_AGGTRADE}:{sym}", sym, StreamType.AGGTRADE)
            self.runtime.watchdog.register_stream(
                f"{STREAM_KLINE}:{sym}", sym, StreamType.KLINE)
            self.runtime.watchdog.register_stream(
                f"{STREAM_OI}:{sym}", sym, StreamType.OI_POLLER)
            self.runtime.watchdog.register_stream(
                f"{STREAM_FUNDING}:{sym}", sym, StreamType.FUNDING_PREMIUM)

        # bounded trade queue
        self._trade_q = asyncio.Queue(maxsize=50000)

        self._aggtrade = AggTradeCollector(
            symbols=self.symbols,
            config=WSStreamConfig(base_url=ws_base, route=self.cfg.app.ws_route_market, proxy=proxy),
            clock=self.runtime.clock,
            on_trade=self._on_trade,
        )
        self._kline = KlineCollector(
            symbols=self.symbols,
            intervals=self._kline_intervals,
            config=WSStreamConfig(base_url=ws_base, route=self.cfg.app.ws_route_market, proxy=proxy),
            clock=self.runtime.clock,
            on_kline=self._on_kline,
        )
        self._oi = OIPoller(
            symbols=self.symbols,
            rate_limiter=self.runtime.rate_limiter,
            base_url=rest_base,
            poll_interval_s=self.cfg.data_health.freshness.oi_poll_interval_s,
            clock=self.runtime.clock,
            on_snapshot=self._on_oi,
        )
        self._funding = FundingPremiumCollector(
            symbols=self.symbols,
            rate_limiter=self.runtime.rate_limiter,
            base_url=rest_base,
            poll_interval_s=self.cfg.data_health.freshness.funding_poll_interval_s,
            clock=self.runtime.clock,
            on_snapshot=self._on_funding,
        )
        await self._aggtrade.start()
        await self._kline.start()
        await self._oi.start()
        await self._funding.start()
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_trades())
        logger.info("deep_scanner_started symbols=%d kline_intervals=%s",
                    len(self.symbols), [i.value for i in self._kline_intervals])

    async def _stop_collectors(self) -> None:
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        for c in (self._aggtrade, self._kline):
            if c:
                await c.stop()
        for c in (self._oi, self._funding):
            if c:
                await c.stop()
        self._aggtrade = self._kline = self._oi = self._funding = None
        self._trade_q = None

    # ── collector 回调（快速入队 / 直接处理）──

    async def _on_trade(self, event) -> None:
        # 入队 bounded queue
        q = self._trade_q
        if q is None:
            return
        try:
            q.put_nowait(event)
            self.runtime.queue_monitor.record_enqueue("trade")
        except asyncio.QueueFull:
            # 丢最旧，防积压
            try:
                q.get_nowait()
                self.runtime.queue_monitor.record_dequeue("trade")
                q.put_nowait(event)
                self.runtime.queue_monitor.record_enqueue("trade")
                logger.warning("trade_queue_full_dropped_oldest")
            except Exception:
                pass

    async def _on_kline(self, event) -> None:
        self.runtime.feature_engine.add_kline(event)
        sid = f"{STREAM_KLINE}:{event.symbol}"
        self.runtime.watchdog.record_event(sid, event.event_time, event.receive_time)
        self.runtime.queue_monitor.record_lag(sid, event.event_time, event.receive_time)

    async def _on_oi(self, event) -> None:
        self.runtime.feature_engine.add_oi_snapshot(event)
        self.runtime.oi_lookup.add_snapshot(event)
        sid = f"{STREAM_OI}:{event.symbol}"
        self.runtime.watchdog.record_event(sid, event.event_time, event.receive_time)

    async def _on_funding(self, event) -> None:
        self.runtime.feature_engine.add_funding_snapshot(event)
        sid = f"{STREAM_FUNDING}:{event.symbol}"
        self.runtime.watchdog.record_event(sid, event.event_time, event.receive_time)

    async def _consume_trades(self) -> None:
        """消费 trade queue → FeatureEngine.add_trade + watchdog。"""
        q = self._trade_q
        while self._running and q is not None:
            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            self.runtime.queue_monitor.record_dequeue("trade")
            sym = event.symbol
            state = self.runtime.get_state(sym)
            state.trade_count += 1
            self.runtime.feature_engine.add_trade(sym, event)
            sid = f"{STREAM_AGGTRADE}:{sym}"
            self.runtime.watchdog.record_event(sid, event.event_time, event.receive_time)
            self.runtime.queue_monitor.record_lag(sid, event.event_time, event.receive_time)

    @property
    def dedup(self):
        return self._aggtrade.dedup if self._aggtrade else None


# ────────────────────────────────────────────────────────────────────
# 主 Runtime
# ────────────────────────────────────────────────────────────────────


class MarketRadarRuntime:
    """市场雷达 runtime — 编排两阶段 Radar + Health + Feature + Detector + StateMachine。"""

    def __init__(self, cfg: AppConfigBundle, clock: Clock | None = None) -> None:
        self.cfg = cfg
        self.clock = clock or SystemClock()
        self.rate_limiter = RateLimiter(
            RateLimiterConfig(
                weight_limit_per_minute=cfg.data_health.rate_limiter.weight_limit_per_minute,
                initial_backoff_ms=cfg.data_health.rate_limiter.initial_backoff_ms,
                max_backoff_ms=cfg.data_health.rate_limiter.max_backoff_ms,
                circuit_breaker_threshold=cfg.data_health.rate_limiter.circuit_breaker_threshold,
            ),
            proxy=cfg.app.proxy,
        )
        self.watchdog = FreshnessWatchdog(
            FreshnessBudget(
                aggtrade_active_ms=cfg.data_health.freshness.aggtrade_active_ms,
                aggtrade_low_activity_ms=cfg.data_health.freshness.aggtrade_low_activity_ms,
                kline_1m_ms=cfg.data_health.freshness.kline_1m_ms,
                oi_poller_ms=cfg.data_health.freshness.oi_poller_multiplier
                * int(cfg.data_health.freshness.oi_poll_interval_s * 1000),
                funding_premium_ms=cfg.data_health.freshness.funding_premium_ms,
            ),
            self.clock,
        )
        self.queue_monitor = QueueLagMonitor(self.clock)
        self.queue_monitor.register_queue("trade")
        self.oi_lookup = OILookup(cfg.data_health.freshness.oi_lookup_tolerance_ms)
        self.feature_engine = build_feature_engine(cfg)
        self.state_machine = build_state_machine(cfg)
        self.confidence = self.state_machine.confidence  # ConfidenceTracker
        self.alerts = AlertManager()
        self.universe = SymbolUniverse(cfg, self.rate_limiter, self.clock)
        self.light_scanner = LightScanner(cfg, self.universe, self.clock)
        self.deep_scanner = DeepScanner(cfg, self)
        self.score_engine = ScoreEngine(cfg.scoring)
        self.confidence_engine = ConfidenceEngine(cfg.scoring)

        # 状态存储
        self.latest_state: dict[str, SymbolRuntimeState] = {}
        self.last_transition: dict[str, AnalysisEvent] = {}
        self.last_evidence_transition: dict[str, AnalysisEvent] = {}
        self.transition_history: list[AnalysisEvent] = []
        self.candidates: list[tuple[str, float]] = []
        # P0.4 Candidate Hysteresis 追踪
        self._deep_entered_at: dict[str, int] = {}  # symbol → 进入 deep set 的时间
        self._deep_drop_count: dict[str, int] = {}  # symbol → 连续跌出次数

        self._tasks: list[asyncio.Task] = []
        self._running = False

    def get_state(self, symbol: str) -> SymbolRuntimeState:
        if symbol not in self.latest_state:
            self.latest_state[symbol] = SymbolRuntimeState(symbol=symbol)
        return self.latest_state[symbol]

    # ── 生命周期 ──

    async def start(self) -> None:
        self._running = True
        # 初始 universe
        await self.universe.refresh()
        # 初始 deep set = top deep_max_symbols by light score（首次用 volume 排序兜底）
        max_deep = self.cfg.hysteresis.max_deep_symbols
        initial = self.universe.universe[:max_deep]
        await self.deep_scanner.set_symbols(initial)
        now_ms = self.clock.now_ms()
        for sym in initial:
            self._deep_entered_at[sym] = now_ms
            self._deep_drop_count[sym] = 0
        # 周期任务
        self._tasks = [
            asyncio.create_task(self._universe_loop()),
            asyncio.create_task(self._candidate_loop()),
            asyncio.create_task(self._compute_loop()),
        ]
        logger.info("runtime_started universe=%d deep=%d",
                    len(self.universe.universe), len(self.deep_scanner.symbols))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks = []
        await self.deep_scanner.set_symbols([])
        await self.rate_limiter.close()

    async def _universe_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.cfg.app.candidate_refresh_interval_s)
                await self.universe.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("universe_loop_error")

    async def _candidate_loop(self) -> None:
        """Stage1 扫描 → 更新候选 → 防抖过滤 → 调整 deep set。

        P0.4 Candidate Hysteresis：
        - 进入 Deep Set 后最低驻留 min_dwell_s
        - 连续 min_consecutive_drops 次跌出阈值后才移除
        - 新 symbol 增量 subscribe，移除 symbol 增量 unsubscribe
        """
        hyst = self.cfg.hysteresis
        while self._running:
            try:
                await asyncio.sleep(self.cfg.app.light_scan_interval_s)
                self.candidates = self.light_scanner.scan()
                now_ms = self.clock.now_ms()

                # 期望 deep set = 候选 top + 保底
                max_deep = hyst.max_deep_symbols
                desired = [s for s, _ in self.candidates[:max_deep]]
                # 补足至 max_deep（用 universe 前 N 兜底，保证 WS 不空）
                for sym in self.universe.universe:
                    if len(desired) >= max_deep:
                        break
                    if sym not in desired:
                        desired.append(sym)
                desired_set = set(desired)
                current_set = set(self.deep_scanner.symbols)

                # 防抖过滤
                to_keep = set()
                for sym in current_set:
                    if sym in desired_set:
                        # 仍在期望集中 — 重置跌出计数
                        self._deep_drop_count[sym] = 0
                        to_keep.add(sym)
                    else:
                        # 跌出期望集 — 检查驻留时间和连续跌出次数
                        entered_at = self._deep_entered_at.get(sym, now_ms)
                        dwell_s = (now_ms - entered_at) / 1000.0
                        drops = self._deep_drop_count.get(sym, 0) + 1
                        self._deep_drop_count[sym] = drops

                        if dwell_s < hyst.min_dwell_s or drops < hyst.min_consecutive_drops:
                            # 驻留不足或跌出次数不足 — 保留
                            to_keep.add(sym)
                        else:
                            # 满足移除条件 — 清理追踪
                            self._deep_entered_at.pop(sym, None)
                            self._deep_drop_count.pop(sym, None)

                # 新增 symbol
                to_add = desired_set - to_keep
                final_set = to_keep | to_add

                # 更新进入时间
                for sym in to_add:
                    self._deep_entered_at[sym] = now_ms
                    self._deep_drop_count[sym] = 0

                await self.deep_scanner.set_symbols(list(final_set))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("candidate_loop_error")

    async def _compute_loop(self) -> None:
        """周期计算 feature + confidence + state machine。"""
        while self._running:
            try:
                now = self.clock.now_ms()
                for sym in self.deep_scanner.symbols:
                    self._compute_symbol(sym, now)
                await asyncio.sleep(self.cfg.app.deep_compute_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("compute_loop_error")

    def _sync_connected(self) -> None:
        """把 collector 实际连接状态同步到 watchdog。"""
        agg_connected = self.deep_scanner._aggtrade.stats.connected if self.deep_scanner._aggtrade else False
        kline_connected = self.deep_scanner._kline.stats.connected if self.deep_scanner._kline else False
        # REST poller 无长连接概念：运行中即视为 connected
        oi_connected = self.deep_scanner._oi is not None and self.deep_scanner._oi._running
        funding_connected = self.deep_scanner._funding is not None and self.deep_scanner._funding._running
        for sym in self.deep_scanner.symbols:
            self.watchdog.mark_connected(f"{STREAM_AGGTRADE}:{sym}", agg_connected)
            self.watchdog.mark_connected(f"{STREAM_KLINE}:{sym}", kline_connected)
            self.watchdog.mark_connected(f"{STREAM_OI}:{sym}", oi_connected)
            self.watchdog.mark_connected(f"{STREAM_FUNDING}:{sym}", funding_connected)

    def _compute_symbol(self, symbol: str, now: int) -> None:
        """单 symbol：health → confidence → feature → state machine → 状态存储。"""
        # 0. 同步 connected 状态（watchdog 自身不感知 collector 连接）
        self._sync_connected()
        # 1. health
        health_statuses = [
            self.watchdog.check_health(f"{STREAM_AGGTRADE}:{symbol}"),
            self.watchdog.check_health(f"{STREAM_KLINE}:{symbol}"),
            self.watchdog.check_health(f"{STREAM_OI}:{symbol}"),
            self.watchdog.check_health(f"{STREAM_FUNDING}:{symbol}"),
        ]
        confidence = self.confidence.update(symbol, health_statuses)
        health_summary = {hs.stream: hs.status.value for hs in health_statuses}
        self.feature_engine.set_health(symbol, health_summary)

        # 2. feature
        snap = self.feature_engine.compute_snapshot(symbol, now)

        # 3. state machine
        event = self.state_machine.process(snap, now)

        # 4. 更新 latest_state（CurrentState）
        ssm = self.state_machine.get_symbol(symbol)
        st = self.get_state(symbol)
        st.state = ssm.state
        st.direction = ssm.direction
        st.confidence_state = confidence
        st.features = {k: (v.value if v.available else None) for k, v in snap.features.items()}
        st.health = health_summary
        st.last_update_ms = now
        if st.state_since_ms is None:
            st.state_since_ms = now
        # light info
        tk = self.universe.get_ticker(symbol)
        st.price_change_24h = tk.get("price_change_pct", 0.0)
        st.quote_volume_24h = tk.get("quote_volume", 0.0)
        # evidence/veto 计数取自最近含证据的 transition（自动 COOLDOWN 不擦除证据）
        le = self.last_evidence_transition.get(symbol)
        if le is not None:
            st.evidence_count = len(le.evidence)
            st.veto_count = len(le.vetoes)
            st.last_transition_at = le.asof

        # 4.5 评分 + 置信度 + 翻译（V1.1）
        sample_count = len(self.feature_engine.get_state(symbol).baseline_volumes)
        score_bd = self.score_engine.compute(
            snap=snap,
            state=st.state,
            direction=st.direction.value if st.direction else None,
            evidence_count=st.evidence_count,
            state_since_ms=st.state_since_ms,
            now_ms=now,
            sample_count=sample_count,
        )
        st.opportunity_score = score_bd.opportunity_score
        st.score_available = score_bd.available
        st.score_breakdown = score_bd.to_dict()

        conf_bd = self.confidence_engine.compute(
            confidence_state=confidence,
            snap=snap,
            evidence_count=st.evidence_count,
            sample_count=sample_count,
        )
        st.confidence = conf_bd.confidence
        st.confidence_available = conf_bd.available
        st.confidence_breakdown = conf_bd.to_dict()

        # stale_flag
        stale_fv = snap.features.get("stale_flag")
        st.stale_flag = stale_fv.value if stale_fv and stale_fv.available else 0.0

        # 一句话结论
        st.summary = PresentationTranslator.generate_summary(
            st.state, st.direction.value if st.direction else None,
            score_bd, conf_bd,
        )

        # 5. 真实 transition → 存 last_transition + history
        if event is not None:
            self.last_transition[symbol] = event
            self.transition_history.append(event)
            # 含证据/veto 的 transition 单独保留（P0.2：Evidence 不被自动迁移擦除）
            if event.evidence or event.vetoes:
                self.last_evidence_transition[symbol] = event
            st.evidence_count = len(event.evidence) if (event.evidence or event.vetoes) else st.evidence_count
            st.veto_count = len(event.vetoes) if (event.evidence or event.vetoes) else st.veto_count
            if event.evidence or event.vetoes:
                st.last_transition_at = event.asof
            st.state_since_ms = now
            # Alerts（仅消费 AnalysisEvent）
            self.alerts.process_event(event, now)
            logger.info(
                "[transition] %s %s→%s %s evid=%d veto=%d",
                symbol, event.previous_state.value, event.new_state.value,
                event.direction.value if event.direction else "-", len(event.evidence), len(event.vetoes),
            )

    # ── 数据访问（Dashboard 用）──

    def get_radar(self) -> list[dict[str, Any]]:
        """Market Radar 卡片列表（按状态优先级 + evidence 排序）。"""
        order = {
            State.START_CONFIRMED: 0, State.CONTINUATION: 1, State.SUSPECTED_START: 2,
            State.ANOMALY: 3, State.EXHAUSTION: 4, State.WITHDRAWAL: 5,
            State.REJECTED: 6, State.COOLDOWN: 7, State.SLEEPING: 8,
        }
        items = list(self.latest_state.values())
        items.sort(key=lambda s: (order.get(s.state, 9), -s.evidence_count, -s.light_score,
                                  -(s.last_update_ms or 0)))
        result = []
        for s in items:
            result.append({
                "symbol": s.symbol,
                "state": s.state.value,
                "state_label": PresentationTranslator.state_label(s.state),
                "state_display": PresentationTranslator.state_display(s.state),
                "direction": s.direction.value if s.direction else None,
                "direction_label": PresentationTranslator.direction_label(s.direction),
                "confidence_state": s.confidence_state.value,
                "confidence_state_label": PresentationTranslator.confidence_label(s.confidence_state),
                "opportunity_score": round(s.opportunity_score, 1) if s.score_available else None,
                "score_available": s.score_available,
                "confidence": round(s.confidence, 4) if s.confidence_available else None,
                "confidence_pct": round(s.confidence * 100, 1) if s.confidence_available else None,
                "confidence_available": s.confidence_available,
                "summary": s.summary,
                "price_change_24h": s.price_change_24h,
                "quote_volume_24h": s.quote_volume_24h,
                "evidence_count": s.evidence_count,
                "veto_count": s.veto_count,
                "stale_flag": s.stale_flag,
                "last_update_ms": s.last_update_ms,
                "state_since_ms": s.state_since_ms,
                "last_transition_at": s.last_transition_at,
            })
        return result

    def get_symbol_detail(self, symbol: str) -> dict[str, Any] | None:
        s = self.latest_state.get(symbol)
        if s is None:
            return None
        le = self.last_evidence_transition.get(symbol)
        evidence_list = [_ev_dict(e) for e in (le.evidence if le else [])]
        veto_list = [_veto_dict(v) for v in (le.vetoes if le else [])]

        # 特征值 dict（供翻译层用）
        fv = s.features

        # 翻译模块
        capital_flow = PresentationTranslator.translate_capital_flow(fv)
        volume_price = PresentationTranslator.translate_volume_price(fv)
        false_start = PresentationTranslator.translate_false_start_check(veto_list)

        # 状态时间轴
        timeline = [
            {
                "asof": e.asof,
                "previous_state": e.previous_state.value,
                "new_state": e.new_state.value,
            }
            for e in self.transition_history
            if e.symbol == symbol
        ][-20:]
        timeline_translated = PresentationTranslator.translate_timeline(timeline)

        return {
            "symbol": symbol,
            "state": s.state.value,
            "state_label": PresentationTranslator.state_label(s.state),
            "state_display": PresentationTranslator.state_display(s.state),
            "direction": s.direction.value if s.direction else None,
            "direction_label": PresentationTranslator.direction_label(s.direction),
            "confidence_state": s.confidence_state.value,
            "confidence_state_label": PresentationTranslator.confidence_label(s.confidence_state),
            "state_since_ms": s.state_since_ms,
            "features": s.features,
            "health": s.health,
            "evidence": evidence_list,
            "vetoes": veto_list,
            "last_transition_at": s.last_transition_at,
            # V1.1 评分
            "opportunity_score": round(s.opportunity_score, 1) if s.score_available else None,
            "score_available": s.score_available,
            "score_breakdown": s.score_breakdown,
            "confidence": round(s.confidence, 4) if s.confidence_available else None,
            "confidence_pct": round(s.confidence * 100, 1) if s.confidence_available else None,
            "confidence_available": s.confidence_available,
            "confidence_breakdown": s.confidence_breakdown,
            "summary": s.summary,
            "stale_flag": s.stale_flag,
            # 翻译模块
            "capital_flow": capital_flow,
            "volume_price": volume_price,
            "false_start_check": false_start,
            "timeline": timeline_translated,
            "subscore_labels": PresentationTranslator.subscore_labels(),
        }

    def get_health(self) -> list[dict[str, Any]]:
        """数据健康表。"""
        # HealthLevel → 中文
        health_labels = {
            "OK": "正常", "WARN": "预热中", "STALE": "数据延迟",
            "DRIFT": "数据偏移", "FAIL": "数据异常",
        }
        # ConfidenceState → 中文
        conf_labels = {
            "CONFIDENT": "可信", "DEGRADED": "降级", "UNKNOWN": "不足",
        }
        result = []
        for sym in self.deep_scanner.symbols:
            row = {"symbol": sym}
            for prefix in (STREAM_AGGTRADE, STREAM_KLINE, STREAM_OI, STREAM_FUNDING):
                hs = self.watchdog.check_health(f"{prefix}:{sym}")
                raw_status = hs.status.value
                row[prefix] = {
                    "status": raw_status,
                    "status_label": health_labels.get(raw_status, raw_status),
                    "age_ms": hs.age_ms,
                    "connected": hs.connected,
                    "last_event_time": hs.last_event_time,
                }
            raw_conf = self.confidence.get(sym).value
            row["confidence_state"] = raw_conf
            row["confidence_state_label"] = conf_labels.get(raw_conf, raw_conf)
            result.append(row)
        return result

    def get_signal_history(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        events = self.transition_history
        if symbol:
            events = [e for e in events if e.symbol == symbol]
        events = events[-limit:]
        return [{
            "symbol": e.symbol,
            "state": e.new_state.value,
            "state_label": PresentationTranslator.state_label(e.new_state),
            "state_display": PresentationTranslator.state_display(e.new_state),
            "direction": e.direction.value if e.direction else None,
            "direction_label": PresentationTranslator.direction_label(e.direction),
            "asof": e.asof,
            "evidence_count": len(e.evidence),
            "veto_count": len(e.vetoes),
        } for e in events]

    def get_stats(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for s in self.latest_state.values():
            counts[s.state.value] = counts.get(s.state.value, 0) + 1
        # P0.5: 人类可读数据状态
        any_stale = any(s.stale_flag > 0 for s in self.latest_state.values())
        any_fail = any(
            v in ("FAIL", "STALE") for s in self.latest_state.values() for v in s.health.values()
        )
        # 整体 confidence 状态
        all_confident = all(
            s.confidence_state == ConfidenceState.CONFIDENT
            for s in self.latest_state.values()
        ) if self.latest_state else False
        any_unknown = any(
            s.confidence_state == ConfidenceState.UNKNOWN
            for s in self.latest_state.values()
        )
        if any_fail or any_unknown:
            data_status = "数据异常"
        elif any_stale:
            data_status = "数据延迟"
        elif all_confident:
            data_status = "数据正常"
        else:
            data_status = "数据降级"

        return {
            "universe_size": len(self.universe.universe),
            "deep_size": len(self.deep_scanner.symbols),
            "candidate_count": len(self.candidates),
            "state_counts": counts,
            "data_status": data_status,
            "queue_depth": self.queue_monitor.get_queue_metrics("trade").depth if self.queue_monitor.get_queue_metrics("trade") else 0,
            "rate_limiter": {
                "weight_used": self.rate_limiter.state.weight_used,
                "total_429": self.rate_limiter.state.total_429,
                "total_418": self.rate_limiter.state.total_418,
                "circuit_open": self.rate_limiter.state.circuit_open,
            },
        }

    def get_top10(self) -> list[dict[str, Any]]:
        """Top10 排名 — 按 RankingScore 排序。"""
        radar = self.get_radar()
        return rank_symbols(radar, top_n=10)

    def get_market_summary(self) -> dict[str, Any]:
        """市场总览 — 系统结论 + 统计。"""
        top10 = self.get_top10()
        stats = self.get_stats()
        conclusion = generate_system_conclusion(top10, stats.get("candidate_count", 0))
        return {
            "conclusion": conclusion,
            "data_status": stats.get("data_status", "未知"),
            "universe_size": stats.get("universe_size", 0),
            "candidate_count": stats.get("candidate_count", 0),
            "state_counts": stats.get("state_counts", {}),
            "top10": top10,
        }


def _ev_dict(e) -> dict[str, Any]:
    return {
        "family": e.family.value, "type": e.type, "window": e.window,
        "value": e.value, "threshold": e.threshold, "passed": e.passed, "source": e.source,
    }


def _veto_dict(v) -> dict[str, Any]:
    return {"type": v.type.value, "triggered": v.triggered, "severity": v.severity.value, "detail": v.detail}
