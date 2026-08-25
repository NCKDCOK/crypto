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
from pathlib import Path
from typing import Any

import httpx

from src.clock import Clock, SystemClock
from src.collectors.aggtrade_collector import AggTradeCollector
from src.collectors.base_ws import WSStreamConfig
from src.collectors.funding_collector import FundingPremiumCollector
from src.collectors.kline_collector import KlineCollector
from src.collectors.oi_poller import OIPoller
from src.collectors.symbol_registry import SymbolRegistry
from src.collectors.spot_registry import SpotSymbolRegistry
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
    SystemMode,
)
from src.features.engine import FeatureEngine, WINDOW_BY_MS
from src.health.confidence import ConfidenceTracker
from src.health.coverage import compute_coverage
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
from src.supervision.supervisor import SupervisorEngine
from src.scoring.engine import ScoreEngine
from src.scoring.data_confidence import DataConfidenceEngine, DataConfidenceBreakdown
from src.scoring.signal_confirmation import (
    ConfirmationContext,
    SignalConfirmationEngine,
    SignalConfirmationBreakdown,
)
from src.presentation.translator import PresentationTranslator
from src.presentation.ranking import rank_symbols, generate_system_conclusion
from src.presentation.ranking_hysteresis import RankingHysteresis
from src.market.regime import MarketRegimeEngine, MarketSnapshot, RegimeResult
from src.recovery.manager import RecoveryManager, RecoveryReport
from src.storage.sqlite_repository import SqliteRepository
from src.engines.accumulation import AccumulationEngine
from src.engines.dormant_revival import DormantRevivalEngine
from src.engines.distribution import DistributionEngine
from src.engines.impulse_asymmetry import ImpulseAsymmetryEngine
from src.engines.setup_type import SetupTypeEngine
from src.engines.spot_perp import SpotPerpConfirmationEngine
from src.engines.structure import StructureEngine
from src.engines.volume_profile import VolumeProfileEngine
from src.engines.location import LocationEngine
from src.engines.trend import TrendEngine
from src.engines.pump_risk import PumpRiskEngine
from src.engines.breakout_lifecycle import BreakoutLifecycleEngine
from src.engines.trade_plan import STATUS_ACTIVE, TradePlan, TradePlanEngine
from src.simulation import (
    DecisionSnapshotService,
    EntryRevalidationEngine,
    PaperPositionManager,
    RecommendationSnapshotService,
    SimulationQueueManager,
    SimulationStatistics,
)
from src.simulation.snapshot import FORMAL_STATES

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
        continuation_detector=ContinuationDetector(
            min_oi_maintain=d.continuation_min_oi_maintain,
            min_evidence_count=d.continuation_min_evidence_count,
        ),
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
    # 评分（V1.2 §3-4）：拆分数据可信度 / 信号确认度
    data_confidence: float = 0.0  # 0~100
    data_confidence_available: bool = False
    data_confidence_breakdown: dict[str, Any] = field(default_factory=dict)
    signal_confirmation: float = 0.0  # 0~100
    signal_confirmation_available: bool = False
    signal_confirmation_breakdown: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    stale_flag: float = 0.0
    # V1.2 引擎结果
    setup_type: str = "NONE"
    setup_label: str = "无明确 Setup"
    accumulation_score: float | None = None
    distribution_risk: float | None = None
    revival_score: float | None = None
    impulse_label: str = ""
    spot_perp_label: str = ""
    location_label: str = ""
    trend_score: float | None = None
    trend_label: str = ""
    pump_risk: float | None = None
    trade_plan: dict[str, Any] = field(default_factory=dict)
    # V1.3 §7 监督池元数据（SupervisorEngine 派生，dict 形式供 API/UI）
    supervision: dict[str, Any] = field(default_factory=dict)
    # V1.3 P2 模拟验证：引擎状态 dict（RecommendationSnapshot §20 / Revalidation ctx §26）
    breakout_state: dict[str, Any] = field(default_factory=dict)
    structure_state: dict[str, Any] = field(default_factory=dict)
    spot_perp_state: dict[str, Any] = field(default_factory=dict)
    # V1.3 §11 稳定决策快照（DecisionSnapshotService 冻结；{frozen_at, decision}）
    decision_snapshot: dict[str, Any] = field(default_factory=dict)


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
        self._spot: AggTradeCollector | None = None  # V1.2 §9 现货 aggTrade
        self._spot_symbols: list[str] = []  # 有现货的 deep symbols
        self._trade_q: asyncio.Queue | None = None
        self._spot_q: asyncio.Queue | None = None
        self._consumer_task: asyncio.Task | None = None
        self._spot_consumer_task: asyncio.Task | None = None
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
        # V1.2 §9 现货 aggTrade（仅有现货的 symbol）
        if self.cfg.app.enable_spot:
            self._spot_symbols = [
                s for s in self.symbols if self.runtime.spot_registry.has_spot(s)
            ]
            for s in self._spot_symbols:
                self.runtime.feature_engine.set_spot_available(s, True)
            self._spot_q = asyncio.Queue(maxsize=50000)
            self._spot = AggTradeCollector(
                symbols=self._spot_symbols,
                config=WSStreamConfig(
                    base_url=self.cfg.app.spot_ws_base_url, route="",
                    streams=AggTradeCollector.build_streams(self._spot_symbols),
                    proxy=proxy,
                ),
                clock=self.runtime.clock,
                on_trade=self._on_spot_trade,
            )
            await self._spot.start()
            self._spot_consumer_task = asyncio.create_task(self._consume_spot_trades())
            logger.info("spot_collector_started spot_symbols=%d", len(self._spot_symbols))
        await self._aggtrade.start()
        await self._kline.start()
        await self._oi.start()
        await self._funding.start()
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_trades())
        logger.info("deep_scanner_started symbols=%d kline_intervals=%s spot=%d",
                    len(self.symbols), [i.value for i in self._kline_intervals],
                    len(self._spot_symbols))

    async def _stop_collectors(self) -> None:
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        if self._spot_consumer_task:
            self._spot_consumer_task.cancel()
            try:
                await self._spot_consumer_task
            except asyncio.CancelledError:
                pass
            self._spot_consumer_task = None
        for c in (self._aggtrade, self._kline, self._spot):
            if c:
                await c.stop()
        for c in (self._oi, self._funding):
            if c:
                await c.stop()
        self._aggtrade = self._kline = self._oi = self._funding = self._spot = None
        self._trade_q = None
        self._spot_q = None

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
        # V1.2 持久化 closed bar
        if event.is_closed:
            self.runtime.repository.save_kline(event)
        sid = f"{STREAM_KLINE}:{event.symbol}"
        self.runtime.watchdog.record_event(sid, event.event_time, event.receive_time)
        self.runtime.queue_monitor.record_lag(sid, event.event_time, event.receive_time)

    async def _on_oi(self, event) -> None:
        self.runtime.feature_engine.add_oi_snapshot(event)
        self.runtime.oi_lookup.add_snapshot(event)
        # V1.2 持久化 OI 快照
        self.runtime.repository.save_oi_snapshot(event)
        sid = f"{STREAM_OI}:{event.symbol}"
        self.runtime.watchdog.record_event(sid, event.event_time, event.receive_time)

    async def _on_funding(self, event) -> None:
        self.runtime.feature_engine.add_funding_snapshot(event)
        # V1.2 持久化 Funding 快照
        self.runtime.repository.save_funding_snapshot(event)
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

    # ── V1.2 §9 现货 trade ──

    async def _on_spot_trade(self, event) -> None:
        q = self._spot_q
        if q is None:
            return
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass

    async def _consume_spot_trades(self) -> None:
        """消费现货 trade queue → FeatureEngine.add_spot_trade。"""
        q = self._spot_q
        while self._running and q is not None:
            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            self.runtime.feature_engine.add_spot_trade(event.symbol, event)

    @property
    def dedup(self):
        return self._aggtrade.dedup if self._aggtrade else None


# ────────────────────────────────────────────────────────────────────
# 主 Runtime
# ────────────────────────────────────────────────────────────────────


class MarketRadarRuntime:
    """市场雷达 runtime — 编排两阶段 Radar + Health + Feature + Detector + StateMachine。"""

    def __init__(self, cfg: AppConfigBundle, clock: Clock | None = None,
                 repository: "Repository | None" = None) -> None:
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
        self.push_history: list[dict[str, Any]] = []  # V1.2 §37 推送历史
        self.universe = SymbolUniverse(cfg, self.rate_limiter, self.clock)
        self.light_scanner = LightScanner(cfg, self.universe, self.clock)
        self.deep_scanner = DeepScanner(cfg, self)
        # V1.2 §9 现货注册表
        self.spot_registry = SpotSymbolRegistry(quote_asset=cfg.symbols.quote_asset)
        self.score_engine = ScoreEngine(cfg.scoring)
        self.data_confidence_engine = DataConfidenceEngine(cfg.scoring)
        self.signal_confirmation_engine = SignalConfirmationEngine(cfg.scoring)

        # V1.2 持久化 + 停机恢复
        if repository is not None:
            self.repository = repository
        else:
            data_dir = Path(cfg.app.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            self.repository = SqliteRepository(data_dir / "radar.db")
        self.recovery_manager = RecoveryManager(
            repo=self.repository,
            cfg=cfg.recovery,
            clock=self.clock,
            rate_limiter=self.rate_limiter,
            rest_base_url=cfg.app.rest_base_url,
            feature_engine=self.feature_engine,
        )
        # V1.3 §47：启动默认 BOOTSTRAP，恢复流程完成后由 _run_recovery 置为 RECOVERY/WARMUP
        self.system_mode: SystemMode = SystemMode.BOOTSTRAP
        self.recovery_report: RecoveryReport | None = None
        # V1.2 §6.4 Top10 排名滞回
        self.ranking_hysteresis = RankingHysteresis()
        # V1.2 §8 市场背景引擎
        self.market_regime_engine = MarketRegimeEngine(cfg.market_regime)
        self.market_regime: RegimeResult | None = None
        # V1.2 行为引擎
        self.accumulation_engine = AccumulationEngine()
        self.dormant_revival_engine = DormantRevivalEngine()
        self.distribution_engine = DistributionEngine()
        self.impulse_engine = ImpulseAsymmetryEngine()
        self.setup_type_engine = SetupTypeEngine()
        self.spot_perp_engine = SpotPerpConfirmationEngine()
        self.structure_engine = StructureEngine()
        self.volume_profile_engine = VolumeProfileEngine()
        self.location_engine = LocationEngine()
        self.trend_engine = TrendEngine()
        self.pump_risk_engine = PumpRiskEngine()
        self.breakout_lifecycle_engine = BreakoutLifecycleEngine()
        self.trade_plan_engine = TradePlanEngine()
        # V1.3 §5-§10 状态监督：池派生映射 + state-aware 监督引擎
        self.supervisor = SupervisorEngine(cfg.supervision)

        # V1.3 P2 模拟验证（§11/§22/§26/§29/§62）：阈值全部从 cfg 注入
        self.decision_snapshot_service = DecisionSnapshotService(
            interval_s=cfg.ranking.decision_snapshot_s,
        )
        self.recommendation_snapshot_service = RecommendationSnapshotService(
            min_opportunity=cfg.ranking.min_opportunity,
            min_signal_confirmation=cfg.ranking.min_signal_confirmation,
            min_data_confidence=cfg.ranking.min_data_confidence,
            max_pump_risk=cfg.ranking.max_pump_risk,
        )
        self.revalidation_engine = EntryRevalidationEngine(
            stale_max_s=cfg.simulation.revalidation_stale_max_s,
            min_data_confidence=cfg.ranking.min_data_confidence,
            max_pump_risk=cfg.ranking.max_pump_risk,
        )
        self.simulation_positions = PaperPositionManager(
            cfg.simulation, repository=self.repository,
        )
        self.simulation_queue = SimulationQueueManager(
            cfg.simulation, self.repository,
            revalidation=self.revalidation_engine,
            positions=self.simulation_positions,
        )
        self.simulation_stats = SimulationStatistics()
        # §19/§22 快照去重：symbol → 已冻结快照的 trade_plan_id 集合
        self._snapshot_plan_ids: dict[str, set[str]] = {}
        # V1.3 §48 重启保留：恢复 队列 / 持仓（内存态），并预置快照去重集合
        self._restore_simulation_state()

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
        # V1.2 §9 初始现货注册表
        if self.cfg.app.enable_spot:
            await self.spot_registry.fetch_from_api(
                self.cfg.app.spot_rest_base_url, proxy=self.cfg.app.proxy)
        # 初始 deep set = top deep_max_symbols by light score（首次用 volume 排序兜底）
        max_deep = self.cfg.hysteresis.max_deep_symbols
        initial = self.universe.universe[:max_deep]
        await self.deep_scanner.set_symbols(initial)
        now_ms = self.clock.now_ms()
        for sym in initial:
            self._deep_entered_at[sym] = now_ms
            self._deep_drop_count[sym] = 0

        # V1.2 停机恢复：后台异步补历史 K 线（不阻塞 server 启动）
        intervals = [i.value for i in self.deep_scanner._kline_intervals] or ["1m", "5m", "15m", "1h"]
        self._recovery_task = asyncio.create_task(self._run_recovery(initial, intervals, now_ms))

        # 周期任务
        self._tasks = [
            asyncio.create_task(self._universe_loop()),
            asyncio.create_task(self._candidate_loop()),
            asyncio.create_task(self._compute_loop()),
        ]
        logger.info("runtime_started universe=%d deep=%d mode=%s",
                    len(self.universe.universe), len(self.deep_scanner.symbols),
                    self.system_mode.value)

    async def _run_recovery(self, symbols: list[str], intervals: list[str], now_ms: int) -> None:
        """后台执行停机恢复（不阻塞 server 启动）。"""
        try:
            self.recovery_report = await self.recovery_manager.recover(symbols, intervals, now_ms)
            self.system_mode = self.recovery_report.mode
            logger.info("recovery mode=%s tier=%s downtime=%.1fs backfilled=%d loaded=%d",
                        self.system_mode.value, self.recovery_report.tier,
                        self.recovery_report.downtime_s, self.recovery_report.klines_backfilled,
                        self.recovery_report.klines_loaded)
        except Exception:
            logger.exception("recovery_failed")

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
        self.repository.close()

    async def _universe_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.cfg.app.candidate_refresh_interval_s)
                await self.universe.refresh()
                # V1.2 §9 刷新现货注册表
                if self.cfg.app.enable_spot:
                    await self.spot_registry.fetch_from_api(
                        self.cfg.app.spot_rest_base_url, proxy=self.cfg.app.proxy)
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
                # V1.2 §8 市场背景（每轮计算，开销小）
                self._compute_market_regime(now)
                # V1.2 模式提升：RECOVERY→WARMUP→LIVE
                self._promote_mode(now)
                await asyncio.sleep(self.cfg.app.deep_compute_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("compute_loop_error")

    def _promote_mode(self, now_ms: int) -> None:
        """根据实时样本积累提升系统模式。"""
        if self.system_mode == SystemMode.LIVE:
            return
        # 统计 deep symbols 的主窗口样本数
        sample_counts = []
        for sym in self.deep_scanner.symbols:
            fe_state = self.feature_engine.get_state(sym)
            sample_counts.append(len(fe_state.baseline_volumes))
        max_samples = max(sample_counts) if sample_counts else 0
        live_min = self.cfg.recovery.live_min_samples

        if self.system_mode == SystemMode.RECOVERY:
            # 实时资金流恢复（有样本） → WARMUP
            if max_samples > 0:
                self.system_mode = SystemMode.WARMUP
                logger.info("mode_promoted RECOVERY→WARMUP samples=%d", max_samples)
        if self.system_mode == SystemMode.WARMUP:
            if max_samples >= live_min:
                self.system_mode = SystemMode.LIVE
                logger.info("mode_promoted WARMUP→LIVE samples=%d", max_samples)

    @property
    def is_live(self) -> bool:
        return self.system_mode == SystemMode.LIVE

    def _compute_market_regime(self, now_ms: int) -> None:
        """构建市场横截面快照并判定 regime（V1.2 §8）。"""
        # breadth from universe tickers
        up = down = 0
        for sym, tk in self.universe.tickers.items():
            pct = tk.get("price_change_pct", 0.0)
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
        universe_size = len(self.universe.universe) or 1
        anomaly_ratio = len(self.candidates) / universe_size if universe_size else 0.0

        # OI 扩张/收缩 from deep symbols
        oi_expand = oi_contract = 0
        for sym in self.deep_scanner.symbols:
            st = self.latest_state.get(sym)
            if st is None:
                continue
            oi_5m = st.features.get("oi_change_5m")
            if oi_5m is None:
                continue
            if oi_5m > 0:
                oi_expand += 1
            elif oi_5m < 0:
                oi_contract += 1
        deep_n = len(self.deep_scanner.symbols) or 1
        oi_exp_ratio = oi_expand / deep_n
        oi_con_ratio = oi_contract / deep_n

        # BTC/ETH returns from deep features（若在 deep set）
        def _ctx_ret(sym: str, iv: str) -> float | None:
            st = self.latest_state.get(sym)
            if st is None:
                return None
            return st.features.get(f"context_{iv}")

        snap = MarketSnapshot(
            btc_return_5m=_ctx_ret("BTCUSDT", "5m"),
            btc_return_15m=_ctx_ret("BTCUSDT", "15m"),
            btc_return_1h=_ctx_ret("BTCUSDT", "1h"),
            eth_return_5m=_ctx_ret("ETHUSDT", "5m"),
            eth_return_15m=_ctx_ret("ETHUSDT", "15m"),
            eth_return_1h=_ctx_ret("ETHUSDT", "1h"),
            breadth_up=up,
            breadth_down=down,
            anomaly_ratio=anomaly_ratio,
            oi_expansion_ratio=oi_exp_ratio,
            oi_contraction_ratio=oi_con_ratio,
        )
        self.market_regime = self.market_regime_engine.compute(snap)

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
        fe_state = self.feature_engine.get_state(symbol)
        sample_count = len(fe_state.baseline_volumes)
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

        conf_bd = self.data_confidence_engine.compute(
            confidence_state=confidence,
            snap=snap,
            sample_count=sample_count,
            spot_available=fe_state.spot_available,
        )
        st.data_confidence = conf_bd.score
        st.data_confidence_available = conf_bd.available
        st.data_confidence_breakdown = conf_bd.to_dict()

        # 信号确认度（V1.2 §3.2）— 后续 Phase 注入 breakout_hold/retest/spot_agreement
        accept_fv = snap.features.get("acceptance")
        spot_agree_fv = snap.features.get("spot_perp_agreement")
        conf_ctx = ConfirmationContext(
            direction=st.direction.value if st.direction else None,
            evidence_count=st.evidence_count,
            veto_count=st.veto_count,
            breakout_acceptance=accept_fv.value if (accept_fv and accept_fv.available) else None,
            spot_perp_agreement=spot_agree_fv.value if (spot_agree_fv and spot_agree_fv.available) else None,
        )
        sig_bd = self.signal_confirmation_engine.compute(
            snap=snap,
            ctx=conf_ctx,
            sample_count=sample_count,
            data_confidence_score=conf_bd.score if conf_bd.available else None,
        )
        st.signal_confirmation = sig_bd.score
        st.signal_confirmation_available = sig_bd.available
        st.signal_confirmation_breakdown = sig_bd.to_dict()

        # stale_flag
        stale_fv = snap.features.get("stale_flag")
        st.stale_flag = stale_fv.value if stale_fv and stale_fv.available else 0.0

        # ── V1.2 行为引擎 ──
        fv_dict = {k: (v.value if v.available else None) for k, v in snap.features.items()}
        current_price = self.universe.get_ticker(symbol).get("last_price", 0.0)

        # 结构 + VP（from kline history）
        klines_15m = self.feature_engine.get_kline_history(symbol, "15m")
        struct = self.structure_engine.compute(klines_15m, current_price=current_price)
        vp = self.volume_profile_engine.compute(klines_15m)
        st.structure_state = struct.to_dict()

        # 吸筹 / 派发 / 复活 / 冲量 / Pump
        accum = self.accumulation_engine.compute(fv_dict, st.direction.value if st.direction else None)
        dist = self.distribution_engine.compute(fv_dict, st.direction.value if st.direction else None)
        revival = self.dormant_revival_engine.compute(fv_dict)
        impulse = self.impulse_engine.compute(fv_dict)
        pump = self.pump_risk_engine.compute(fv_dict)

        st.accumulation_score = accum.accumulation_score
        st.distribution_risk = dist.distribution_risk_score
        st.revival_score = revival.revival_score
        st.impulse_label = impulse.label
        st.pump_risk = pump.pump_risk_score

        # Spot×Perp
        spot_perp = self.spot_perp_engine.compute(fv_dict, st.direction.value if st.direction else None)
        st.spot_perp_label = spot_perp.label
        st.spot_perp_state = spot_perp.to_dict()

        # Setup Type
        setup = self.setup_type_engine.compute(
            st.state, st.direction.value if st.direction else None, fv_dict,
            accumulation_score=accum.accumulation_score,
            distribution_risk=dist.distribution_risk_score,
            revival_score=revival.revival_score,
            leverage_dominant=spot_perp.leverage_dominant,
            spot_confirmed=spot_perp.spot_confirmed,
        )
        st.setup_type = setup.setup_type
        st.setup_label = setup.label

        # 突破生命周期
        kline_5m_state = self.feature_engine.get_state(symbol).klines.get("5m")
        breakout = self.breakout_lifecycle_engine.update(
            symbol, now,
            breakout_level=struct.breakout_level,
            current_price=current_price or None,
            kline_5m=kline_5m_state,
            context_15m=fv_dict.get("context_15m"),
            context_1h=fv_dict.get("context_1h"),
            fv=fv_dict,
        )
        st.breakout_state = breakout.to_dict()

        # 位置
        location = self.location_engine.compute(current_price or None, fv_dict,
                                                structure=struct, volume_profile=vp)
        st.location_label = location.label

        # 趋势
        trend = self.trend_engine.compute(fv_dict, klines=klines_15m, structure=struct)
        st.trend_score = trend.trend_score
        st.trend_label = trend.label

        # Trade Plan（V1.3 §18 状态限制 + §19 版本冻结）
        plan = self.trade_plan_engine.compute(
            current_price or None,
            st.direction.value if st.direction else None,
            structure=struct, volume_profile=vp, atr=struct.atr,
            state=st.state,
            sub_stage=st.setup_type,
        )
        prev_plan = st.trade_plan or {}
        prev_active = prev_plan.get("frozen") and prev_plan.get("status") == STATUS_ACTIVE
        if prev_active and plan.status == STATUS_ACTIVE:
            # 同一正式 Setup 持续：保留冻结快照原样，禁止每秒随价格重算覆盖（§19）
            st.trade_plan = prev_plan
        elif plan.status == STATUS_ACTIVE:
            # 新正式 Setup：旧版 V-n → EXPIRED，冻结 V-(n+1) NEW PLAN（§19）
            self.trade_plan_engine.freeze(plan, now, symbol=symbol)
            self.repository.expire_trade_plans(symbol, now)
            self.repository.save_trade_plan(symbol, now, plan.to_dict())
            st.trade_plan = plan.to_dict()
        else:
            # 离开正式范围：旧计划 → EXPIRED（DB 标记），当前展示最新状态判定
            if prev_plan.get("frozen"):
                self.repository.expire_trade_plans(symbol, now)
            st.trade_plan = plan.to_dict()

        # 评分（注入 pump_risk）
        score_bd = self.score_engine.compute(
            snap=snap, state=st.state,
            direction=st.direction.value if st.direction else None,
            evidence_count=st.evidence_count,
            state_since_ms=st.state_since_ms, now_ms=now,
            sample_count=sample_count,
            setup_type=st.setup_type,
            pump_risk_score=st.pump_risk,
        )

        # 一句话结论
        st.summary = PresentationTranslator.generate_summary(
            st.state, st.direction.value if st.direction else None,
            score_bd, conf_bd, sig_bd,
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
            # V1.2 持久化信号
            self.repository.save_analysis_event(event)
            # Alerts（仅消费 AnalysisEvent；非 LIVE 期不发正式推送）
            if self.system_mode == SystemMode.LIVE:
                self.alerts.process_event(event, now)
                # V1.2 §37 State Transition Push
                push = self.alerts.build_push(
                    event,
                    opportunity_score=st.opportunity_score if st.score_available else None,
                    signal_confirmation=st.signal_confirmation if st.signal_confirmation_available else None,
                    data_confidence=st.data_confidence if st.data_confidence_available else None,
                    setup_type=st.setup_type,
                    trade_plan=st.trade_plan or None,
                    one_line=st.summary,
                )
                if push:
                    self.push_history.append(push.to_dict())
            logger.info(
                "[transition] %s %s→%s %s evid=%d veto=%d",
                symbol, event.previous_state.value, event.new_state.value,
                event.direction.value if event.direction else "-", len(event.evidence), len(event.vetoes),
            )

        # V1.3 §5-§10 状态监督：更新监督池元数据（派生标签来自 setup_type）
        labels: list[str] = []
        if st.setup_type == "DISTRIBUTION":
            labels.append("distribution")
        elif st.setup_type == "PUMP_RISK":
            labels.append("pump_risk")
        elif st.setup_type in ("ACCUMULATION", "BREAKOUT_START", "RETEST_REIGNITION"):
            labels.append(st.setup_type.lower())
        st.supervision = self.supervisor.update(
            symbol, st.state, setup_type=st.setup_type,
            labels=labels, now_ms=now,
        ).to_dict()

        # V1.3 P2：首页稳定决策快照（§11）+ 推荐快照/模拟验证驱动（§22/§29；§47 仅 LIVE）
        self._build_home_decision(symbol, now)
        if self.system_mode == SystemMode.LIVE:
            self._maybe_create_snapshot(symbol, now, current_price)
            self._drive_simulation(symbol, now)

    # ── V1.3 P2 模拟验证：决策快照 / 推荐快照 / 逐 tick 驱动 ──

    def _build_home_decision(self, symbol: str, now: int) -> None:
        """V1.3 §11：冻结稳定决策快照（首页展示层；引擎实时值不受影响）。"""
        st = self.get_state(symbol)
        decision = {
            "symbol": symbol,
            "state": st.state.value,
            "setup_type": st.setup_type,
            "direction": st.direction.value if st.direction else None,
            "opportunity_score": st.opportunity_score if st.score_available else None,
            "signal_confirmation": st.signal_confirmation if st.signal_confirmation_available else None,
            "data_confidence": st.data_confidence if st.data_confidence_available else None,
            "pump_risk": st.pump_risk,
            "accumulation_score": st.accumulation_score,
            "distribution_risk": st.distribution_risk,
            "stale_flag": st.stale_flag,
            "summary": st.summary,
            "trade_plan": st.trade_plan,
        }
        st.decision_snapshot = self.decision_snapshot_service.update(symbol, now, decision)

    def _maybe_create_snapshot(self, symbol: str, now: int, current_price: float) -> None:
        """§22：过 Top 门槛的正式推荐 → 冻结不可变快照 → WATCHING 入队。

        §47 门控在调用方（仅 LIVE）。去重（§19/§22）：同一 trade_plan_id 只冻结
        一份；版本冻结产生新 trade_plan_id 后再建新快照。
        """
        st = self.get_state(symbol)
        plan = st.trade_plan or {}
        plan_id = plan.get("trade_plan_id")
        if not plan_id or plan.get("status") != STATUS_ACTIVE:
            return
        done = self._snapshot_plan_ids.setdefault(symbol, set())
        if plan_id in done:
            return
        passed = self.recommendation_snapshot_service.passes_gate(
            state=st.state,
            opportunity_score=st.opportunity_score if st.score_available else None,
            signal_confirmation=st.signal_confirmation if st.signal_confirmation_available else None,
            data_confidence=st.data_confidence if st.data_confidence_available else None,
            trade_plan=plan,
            pump_risk=st.pump_risk,
            stale_flag=st.stale_flag,
        )
        if not passed:
            return
        # §20 证据 / Veto：最近含证据的 transition（冻结推荐时刻的上下文）
        le = self.last_evidence_transition.get(symbol)
        evidence = [
            {"family": e.family.value, "type": e.type, "window": e.window,
             "value": e.value, "threshold": e.threshold, "passed": e.passed, "source": e.source}
            for e in (le.evidence if le and le.evidence else [])
        ]
        vetoes = [
            {"type": v.type.value, "triggered": v.triggered,
             "severity": v.severity.value, "detail": v.detail}
            for v in (le.vetoes if le and le.vetoes else [])
        ]
        snap = self.recommendation_snapshot_service.build(
            symbol=symbol,
            timestamp=now,
            market_regime=self.market_regime.to_dict() if self.market_regime else {},
            state=st.state,
            setup_type=st.setup_type,
            direction=st.direction.value if st.direction else None,
            current_price=current_price or None,
            opportunity_score=st.opportunity_score if st.score_available else None,
            signal_confirmation=st.signal_confirmation if st.signal_confirmation_available else None,
            data_confidence=st.data_confidence if st.data_confidence_available else None,
            all_subscores={
                **st.score_breakdown,
                **st.data_confidence_breakdown,
                **st.signal_confirmation_breakdown,
            },
            all_evidence=evidence,
            all_vetoes=vetoes,
            breakout_state=st.breakout_state,
            structure_state=st.structure_state,
            spot_perp_state=st.spot_perp_state,
            trade_plan=plan,
        )
        done.add(plan_id)
        self.repository.save_recommendation_snapshot(symbol, now, snap.to_dict())
        self.simulation_queue.create_from_snapshot(snap.to_dict(), now)
        logger.info("[simulation] %s 冻结推荐快照 %s", symbol, snap.snapshot_id)

    def _drive_simulation(self, symbol: str, now: int) -> None:
        """V1.3 P2：模拟队列 + 持仓逐 tick 驱动（§47 仅 LIVE 正式）。

        队列不驱动持仓：先 tick 持仓（OPEN 期静态 TP/Stop 优先，§32B），
        再 tick 队列（OPEN → CLOSED 与持仓平仓同 tick 同步，§31）。
        """
        st = self.get_state(symbol)
        ctx = self._build_simulation_ctx(symbol, st, now)
        self.simulation_positions.tick_symbol(symbol, ctx, now)
        self.simulation_queue.tick_symbol(symbol, ctx, now)

    def _build_simulation_ctx(self, symbol: str, st: SymbolRuntimeState, now: int) -> dict[str, Any]:
        """§26 入场二次验证 / §29-§32 持仓退出所需的运行时上下文。

        invalidated：离开正式范围且非 WITHDRAWAL（WITHDRAWAL 由独立确认计时处理）
        即视为原 Setup 失效（§50）。
        """
        ticker = self.universe.get_ticker(symbol)
        price = ticker.get("last_price") or None
        if not price:
            price = st.features.get("price") or None
        data_age_ms = None
        if st.last_update_ms is not None:
            data_age_ms = max(0, now - st.last_update_ms)
        invalidated = st.state.value not in FORMAL_STATES and st.state != State.WITHDRAWAL
        return {
            "price": price,
            "state": st.state.value,
            "setup_type": st.setup_type,
            "direction": st.direction.value if st.direction else None,
            "confidence_state": st.confidence_state.value if st.confidence_state else None,
            "data_confidence": st.data_confidence if st.data_confidence_available else None,
            "data_age_ms": data_age_ms,
            "features": st.features,
            "breakout": st.breakout_state,
            "structure": st.structure_state,
            "spot_perp": st.spot_perp_state,
            "regime": self.market_regime.to_dict() if self.market_regime else None,
            "pump_risk": st.pump_risk,
            "distribution_risk": st.distribution_risk,
            "withdrawal_active": st.state == State.WITHDRAWAL,
            "invalidated": invalidated,
        }

    def _restore_simulation_state(self) -> None:
        """V1.3 §48 重启保留：恢复 队列 / 持仓（内存态），并预置快照去重集合。"""
        try:
            for item_dict in self.repository.list_simulation_queue():
                self.simulation_queue.restore_item(item_dict)
            for pos_dict in self.repository.list_simulation_positions():
                self.simulation_positions.restore_position(pos_dict)
            for item in self.simulation_queue.all():
                plan = (item.snapshot or {}).get("trade_plan") or {}
                pid = plan.get("trade_plan_id")
                if pid:
                    self._snapshot_plan_ids.setdefault(item.symbol, set()).add(pid)
        except Exception:
            logger.exception("[simulation] §48 恢复模拟状态失败，跳过（不影响启动）")

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
                "data_confidence": round(s.data_confidence, 1) if s.data_confidence_available else None,
                "data_confidence_pct": round(s.data_confidence, 1) if s.data_confidence_available else None,
                "data_confidence_available": s.data_confidence_available,
                "data_confidence_breakdown": s.data_confidence_breakdown,
                "signal_confirmation": round(s.signal_confirmation, 1) if s.signal_confirmation_available else None,
                "signal_confirmation_pct": round(s.signal_confirmation, 1) if s.signal_confirmation_available else None,
                "signal_confirmation_available": s.signal_confirmation_available,
                "signal_confirmation_breakdown": s.signal_confirmation_breakdown,
                # deprecated aliases (V1.1) → 映射到 data_confidence，P20 移除
                "confidence": round(s.data_confidence / 100.0, 4) if s.data_confidence_available else None,
                "confidence_pct": round(s.data_confidence, 1) if s.data_confidence_available else None,
                "confidence_available": s.data_confidence_available,
                "summary": s.summary,
                "price_change_24h": s.price_change_24h,
                "quote_volume_24h": s.quote_volume_24h,
                "current_price": self.universe.get_ticker(s.symbol).get("last_price", 0.0),
                # V1.2 引擎结果
                "setup_type": s.setup_type,
                "setup_label": s.setup_label,
                "accumulation_score": s.accumulation_score,
                "distribution_risk": s.distribution_risk,
                "revival_score": s.revival_score,
                "impulse_label": s.impulse_label,
                "spot_perp_label": s.spot_perp_label,
                "location_label": s.location_label,
                "trend_score": s.trend_score,
                "trend_label": s.trend_label,
                "pump_risk": s.pump_risk,
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
            # V1.2 评分（§3-4）
            "data_confidence": round(s.data_confidence, 1) if s.data_confidence_available else None,
            "data_confidence_pct": round(s.data_confidence, 1) if s.data_confidence_available else None,
            "data_confidence_available": s.data_confidence_available,
            "data_confidence_breakdown": s.data_confidence_breakdown,
            "signal_confirmation": round(s.signal_confirmation, 1) if s.signal_confirmation_available else None,
            "signal_confirmation_pct": round(s.signal_confirmation, 1) if s.signal_confirmation_available else None,
            "signal_confirmation_available": s.signal_confirmation_available,
            "signal_confirmation_breakdown": s.signal_confirmation_breakdown,
            # deprecated aliases (V1.1) → data_confidence
            "confidence": round(s.data_confidence / 100.0, 4) if s.data_confidence_available else None,
            "confidence_pct": round(s.data_confidence, 1) if s.data_confidence_available else None,
            "confidence_available": s.data_confidence_available,
            "confidence_breakdown": s.data_confidence_breakdown,
            "summary": s.summary,
            "stale_flag": s.stale_flag,
            # 翻译模块
            "capital_flow": capital_flow,
            "volume_price": volume_price,
            "false_start_check": false_start,
            "timeline": timeline_translated,
            "subscore_labels": PresentationTranslator.subscore_labels(),
            # V1.2 引擎详情
            "setup_type": s.setup_type,
            "setup_label": s.setup_label,
            "trade_plan": s.trade_plan,
            "accumulation_score": s.accumulation_score,
            "distribution_risk": s.distribution_risk,
            "revival_score": s.revival_score,
            "impulse_label": s.impulse_label,
            "spot_perp_label": s.spot_perp_label,
            "location_label": s.location_label,
            "trend_score": s.trend_score,
            "trend_label": s.trend_label,
            "pump_risk": s.pump_risk,
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

    def get_health_coverage(self) -> dict[str, Any]:
        """数据健康覆盖率（V1.3 §46）。

        覆盖率 = OK/WARN 的 symbol×stream 配对 / 全部配对。
        只因为某一个币 OI 延迟不再导致"数据异常"，而是按覆盖率分级；
        核心数据源（aggTrade）整体断线 → 严重异常（无论覆盖率）。
        """
        hc = self.cfg.health_coverage
        pairs: list[tuple[str, str | HealthLevel]] = []
        for sym in self.deep_scanner.symbols:
            for prefix in (STREAM_AGGTRADE, STREAM_KLINE, STREAM_OI, STREAM_FUNDING):
                hs = self.watchdog.check_health(f"{prefix}:{sym}")
                pairs.append((f"{prefix}:{sym}", hs.status))
        result = compute_coverage(
            pairs,
            ok_min=hc.ok_min,
            degraded_min=hc.degraded_min,
            critical_stream_prefix=hc.critical_stream_prefix,
        )
        return result.to_dict()

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
        """Top10 排名 — 按 RankingScore 排序 + 排名滞回（V1.2 §6.4）。

        V1.2：非 LIVE 模式（RECOVERY/WARMUP）不产出强确认 Top10。
        V1.3 §13：正式 Top 机会应用严格阈值（机会≥70 / 信号确认≥75 /
        数据可信≥85 / 上限 10 / 仅 START_CONFIRMED、CONTINUATION），
        COOLDOWN 等状态天然被过滤，不足 10 个不强制凑满。
        """
        if not self.is_live:
            return []
        rk = self.cfg.ranking
        radar = self.get_radar()
        ranked = rank_symbols(
            radar,
            top_n=rk.max_items,
            min_opportunity=rk.min_opportunity,
            min_signal_confirmation=rk.min_signal_confirmation,
            min_data_confidence=rk.min_data_confidence,
            allowed_states=rk.allowed_states,
        )
        now_ms = self.clock.now_ms()
        return self.ranking_hysteresis.update(ranked, now_ms)

    def get_prices(self) -> dict[str, float]:
        """轻量价格快照（供前端 1-2s 轮询当前价）。"""
        prices: dict[str, float] = {}
        for sym in self.deep_scanner.symbols:
            tk = self.universe.get_ticker(sym)
            lp = tk.get("last_price", 0.0)
            if lp:
                prices[sym] = lp
        return prices

    def get_market_summary(self) -> dict[str, Any]:
        """市场总览 — 系统结论 + 统计。"""
        top10 = self.get_top10()
        stats = self.get_stats()
        if not self.is_live:
            mode_label = {
                SystemMode.BOOTSTRAP: "系统启动中（初始化组件/等待恢复完成）",
                SystemMode.RECOVERY: "系统恢复中（补历史/重建结构）",
                SystemMode.WARMUP: "系统预热中（OI/CVD/Delta 基线建立中）",
            }.get(self.system_mode, "系统启动中")
            return {
                "conclusion": f"{mode_label}，暂不产出强确认 Top10 与正式推送。",
                "system_mode": self.system_mode.value,
                "market_regime": self.market_regime.to_dict() if self.market_regime else None,
                "data_status": stats.get("data_status", "未知"),
                "universe_size": stats.get("universe_size", 0),
                "candidate_count": stats.get("candidate_count", 0),
                "state_counts": stats.get("state_counts", {}),
                "top10": [],
                "recovery": _recovery_dict(self.recovery_report),
            }
        conclusion = generate_system_conclusion(top10, stats.get("candidate_count", 0))
        return {
            "conclusion": conclusion,
            "system_mode": self.system_mode.value,
            "market_regime": self.market_regime.to_dict() if self.market_regime else None,
            "data_status": stats.get("data_status", "未知"),
            "universe_size": stats.get("universe_size", 0),
            "candidate_count": stats.get("candidate_count", 0),
            "state_counts": stats.get("state_counts", {}),
            "top10": top10,
            "recovery": _recovery_dict(self.recovery_report),
        }


def _ev_dict(e) -> dict[str, Any]:
    return {
        "family": e.family.value, "type": e.type, "window": e.window,
        "value": e.value, "threshold": e.threshold, "passed": e.passed, "source": e.source,
    }


def _veto_dict(v) -> dict[str, Any]:
    return {"type": v.type.value, "triggered": v.triggered, "severity": v.severity.value, "detail": v.detail}


def _recovery_dict(report: RecoveryReport | None) -> dict[str, Any]:
    if report is None:
        return {}
    return {
        "fresh_start": report.fresh_start,
        "downtime_s": round(report.downtime_s, 1),
        "tier": report.tier,
        "klines_backfilled": report.klines_backfilled,
        "klines_loaded": report.klines_loaded,
        "trade_plans_expired": report.trade_plans_expired,
        "notes": list(report.notes),
    }
