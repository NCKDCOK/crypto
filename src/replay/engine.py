"""Replay Engine — 确定性重放（与 live 同一套核心逻辑）。

依据：SYSTEM_DESIGN.md §12, 改造任务文档 §19, TESTING.md §4
- 事件按 event_time 顺序重放（同时间按 trade_id）
- 使用 TestClock，不依赖 wall time
- 相同输入 → 相同 feature/state 输出（deterministic）
- confidence 由 FreshnessWatchdog + ConfidenceTracker 真实派生（不再注入 override）
  未提供的关键流可simulate_healthy_streams 模拟为 OK（仅 replay，记录于 provenance）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from src.clock import TestClock
from src.domain import (
    AnalysisEvent,
    ConfidenceState,
    FeatureSnapshot,
    FundingRateSnapshot,
    HealthLevel,
    HealthStatus,
    KlineEvent,
    OpenInterestSnapshot,
    TradeEvent,
)
from src.features.engine import FeatureEngine
from src.health.confidence import ConfidenceTracker
from src.health.freshness_watchdog import FreshnessBudget, FreshnessWatchdog, StreamType
from src.state_machine.machine import StateMachine


@dataclass
class ReplayInput:
    """replay 输入（多流）。"""

    trades: list[TradeEvent] = field(default_factory=list)
    oi_snapshots: list[OpenInterestSnapshot] = field(default_factory=list)
    klines: list[KlineEvent] = field(default_factory=list)
    funding_snapshots: list[FundingRateSnapshot] = field(default_factory=list)


@dataclass
class ReplayResult:
    """单次 replay 的结果。"""

    events_processed: int
    transitions: list[AnalysisEvent] = field(default_factory=list)
    snapshots: list[FeatureSnapshot] = field(default_factory=list)
    final_states: dict[str, str] = field(default_factory=dict)


def _stream_id(prefix: str, symbol: str) -> str:
    return f"{prefix}:{symbol}"


class ReplayEngine:
    """确定性 replay 引擎。

    按 event_time 顺序重放多流事件，通过 FeatureEngine + StateMachine
    产出特征快照和状态转换。confidence 由 health 真实派生。结果必须可重复。
    """

    def __init__(
        self,
        feature_engine: FeatureEngine | None = None,
        state_machine: StateMachine | None = None,
        simulate_healthy_streams: bool = False,
    ) -> None:
        self.feature_engine = feature_engine or FeatureEngine()
        self.state_machine = state_machine or StateMachine()
        self.simulate_healthy_streams = simulate_healthy_streams

    def replay(self, data: ReplayInput | Sequence[TradeEvent]) -> ReplayResult:
        """重放事件序列。

        Args:
            data: ReplayInput（多流）或纯 TradeEvent 列表（向后兼容）。

        Returns:
            ReplayResult
        """
        if isinstance(data, ReplayInput):
            trades = data.trades
            oi = data.oi_snapshots
            klines = data.klines
            funding = data.funding_snapshots
        else:
            trades = list(data)
            oi, klines, funding = [], [], []

        if not trades and not oi and not klines and not funding:
            return ReplayResult(events_processed=0)

        # 排序：event_time，同时间按 trade_id（仅 trades）
        sorted_trades = sorted(trades, key=lambda t: (t.event_time, t.trade_id))
        sorted_oi = sorted(oi, key=lambda s: s.receive_time)
        sorted_klines = sorted(klines, key=lambda k: k.event_time)
        sorted_funding = sorted(funding, key=lambda f: f.receive_time)

        all_times = (
            [t.receive_time for t in sorted_trades]
            + [s.receive_time for s in sorted_oi]
            + [k.event_time for k in sorted_klines]
            + [f.receive_time for f in sorted_funding]
        )
        start_time = min(all_times) if all_times else 0
        clock = TestClock(initial_ms=start_time)

        symbols = sorted({t.symbol for t in sorted_trades} | {s.symbol for s in sorted_oi}
                         | {k.symbol for k in sorted_klines} | {f.symbol for f in sorted_funding})

        # Health: 注册关键流
        watchdog = FreshnessWatchdog(FreshnessBudget(), clock)
        confidence_tracker = self.state_machine.confidence
        for sym in symbols:
            watchdog.register_stream(_stream_id("aggTrade", sym), sym, StreamType.AGGTRADE)
            watchdog.register_stream(_stream_id("kline", sym), sym, StreamType.KLINE)
            watchdog.register_stream(_stream_id("oi_poller", sym), sym, StreamType.OI_POLLER)
            if self.simulate_healthy_streams:
                for prefix in ("aggTrade", "kline", "oi_poller"):
                    st = watchdog.get_stream(_stream_id(prefix, sym))
                    st.connected = True
                    st.last_receive_time = start_time
                    st.last_event_time = start_time

        # 合并所有事件按时间推进
        events: list[tuple[int, str, object]] = []
        for t in sorted_trades:
            events.append((t.receive_time, "trade", t))
        for s in sorted_oi:
            events.append((s.receive_time, "oi", s))
        for k in sorted_klines:
            events.append((k.event_time, "kline", k))
        for f in sorted_funding:
            events.append((f.receive_time, "funding", f))
        events.sort(key=lambda e: e[0])

        transitions: list[AnalysisEvent] = []
        snapshots: list[FeatureSnapshot] = []
        processed = 0

        for ts, kind, ev in events:
            clock.set(ts)
            sym = getattr(ev, "symbol")
            if kind == "trade":
                self.feature_engine.add_trade(sym, ev)
                watchdog.record_event(_stream_id("aggTrade", sym), ev.event_time, ev.receive_time)
            elif kind == "oi":
                self.feature_engine.add_oi_snapshot(ev)
                watchdog.record_event(_stream_id("oi_poller", sym), ev.event_time, ev.receive_time)
            elif kind == "kline":
                self.feature_engine.add_kline(ev)
                watchdog.record_event(_stream_id("kline", sym), ev.event_time, ev.receive_time)
            elif kind == "funding":
                self.feature_engine.add_funding_snapshot(ev)
            processed += 1

            # 每个 tick 派生 confidence 并计算 snapshot + 状态机
            self._derive_and_process(sym, watchdog, confidence_tracker, ts, transitions, snapshots)

        final_states = {sym: ssm.state.value for sym, ssm in self.state_machine._symbols.items()}
        return ReplayResult(
            events_processed=processed,
            transitions=transitions,
            snapshots=snapshots,
            final_states=final_states,
        )

    def _derive_and_process(
        self,
        symbol: str,
        watchdog: FreshnessWatchdog,
        confidence_tracker: ConfidenceTracker,
        now_ms: int,
        transitions: list[AnalysisEvent],
        snapshots: list[FeatureSnapshot],
    ) -> None:
        health_statuses = [
            watchdog.check_health(_stream_id(p, symbol))
            for p in ("aggTrade", "kline", "oi_poller")
        ]
        if self.simulate_healthy_streams:
            # 模拟缺失流为 OK（仅 replay）
            health_statuses = [
                hs if hs.status != HealthLevel.FAIL else HealthStatus(
                    stream=hs.stream, symbol=hs.symbol, status=HealthLevel.OK,
                    last_event_time=now_ms, last_receive_time=now_ms, age_ms=0,
                    connected=True, subscribed=True, reason="simulated_healthy",
                )
                for hs in health_statuses
            ]
        confidence = confidence_tracker.update(symbol, health_statuses)
        health_summary = {hs.stream: hs.status.value for hs in health_statuses}
        self.feature_engine.set_health(symbol, health_summary)
        snap = self.feature_engine.compute_snapshot(symbol, now_ms)
        snapshots.append(snap)
        event = self.state_machine.process(snap, now_ms)
        if event is not None:
            transitions.append(event)

    def replay_deterministic(self, data: ReplayInput | Sequence[TradeEvent]) -> ReplayResult:
        """确定性重放 — 相同输入必须得到相同输出（连续重放两次验证一致）。"""
        result1 = self.replay(data)
        # 重置引擎（fresh engine + state machine）
        self.feature_engine = FeatureEngine()
        self.state_machine = StateMachine()
        result2 = self.replay(data)

        assert result1.events_processed == result2.events_processed
        assert len(result1.transitions) == len(result2.transitions)
        for t1, t2 in zip(result1.transitions, result2.transitions):
            assert t1.new_state == t2.new_state
            assert t1.previous_state == t2.previous_state
            assert t1.symbol == t2.symbol
        return result2


def save_replay_result(result: ReplayResult, path: Path) -> None:
    """保存 replay 结果到 JSON 文件。"""
    data = {
        "events_processed": result.events_processed,
        "transitions": [
            {
                "symbol": t.symbol,
                "previous_state": t.previous_state.value,
                "new_state": t.new_state.value,
                "direction": t.direction.value if t.direction else None,
                "asof": t.asof,
                "evidence_count": len(t.evidence),
                "veto_count": len(t.vetoes),
            }
            for t in result.transitions
        ],
        "final_states": result.final_states,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_trades_from_jsonl(path: Path) -> list[TradeEvent]:
    """从 JSONL 文件加载 TradeEvent 序列（Binance aggTrade payload）。"""
    from decimal import Decimal
    from src.domain import AggressorSide

    trades: list[TradeEvent] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            m = d.get("m")
            price = Decimal(str(d.get("p", "0")))
            qty = Decimal(str(d.get("q", "0")))
            trades.append(TradeEvent(
                symbol=d.get("s", ""),
                exchange="binance",
                trade_id=int(d.get("a", 0)),
                event_time=int(d.get("T", 0)),
                receive_time=int(d.get("T", 0)),
                price=price,
                qty=qty,
                quote_notional=price * qty,
                aggressor_side=AggressorSide.from_binance_m(m),
                is_maker=bool(m) if m is not None else False,
            ))
    return trades
