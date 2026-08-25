"""Replay Engine — 确定性重放。

依据：SYSTEM_DESIGN.md §12, TESTING.md §4
- 事件按 event_time 顺序重放（同时间按 trade_id）
- 使用 TestClock，不依赖 wall time
- 相同输入 → 相同 feature/state 输出（deterministic）
- 保存原始事件子集 + FeatureSnapshot + 状态转换 + evidence/veto
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from src.clock import TestClock
from src.domain import AnalysisEvent, FeatureSnapshot, TradeEvent
from src.features.engine import FeatureEngine
from src.state_machine.machine import StateMachine
from src.windows.rolling_window import RollingWindow


@dataclass
class ReplayResult:
    """单次 replay 的结果。"""

    events_processed: int
    transitions: list[AnalysisEvent] = field(default_factory=list)
    snapshots: list[FeatureSnapshot] = field(default_factory=list)
    final_states: dict[str, str] = field(default_factory=dict)


@dataclass
class OutcomeRecord:
    """候选事件的后续表现记录。"""

    symbol: str
    state: str
    direction: str | None
    asof: int
    # 后续 1m/5m/15m/1h 最大有利/不利变动
    max_favorable_1m: float | None = None
    max_adverse_1m: float | None = None
    max_favorable_5m: float | None = None
    max_adverse_5m: float | None = None
    duration_s: float | None = None
    is_quick_fail: bool = False


class ReplayEngine:
    """确定性 replay 引擎。

    按事件时间顺序重放 TradeEvent 序列，通过 FeatureEngine + StateMachine
    产出特征快照和状态转换。结果必须可重复。
    """

    def __init__(
        self,
        feature_engine: FeatureEngine | None = None,
        state_machine: StateMachine | None = None,
        window_ms: int = 30_000,
    ) -> None:
        self.feature_engine = feature_engine or FeatureEngine()
        self.state_machine = state_machine or StateMachine()
        self.window_ms = window_ms

    def replay(
        self,
        trades: Sequence[TradeEvent],
        confidence_overrides: dict[str, str] | None = None,
    ) -> ReplayResult:
        """重放 trade 序列。

        Args:
            trades: 按 event_time 排序的 TradeEvent 列表。
            confidence_overrides: symbol → ConfidenceState 覆盖。

        Returns:
            ReplayResult
        """
        if not trades:
            return ReplayResult(events_processed=0)

        # 排序：event_time，同时间按 trade_id
        sorted_trades = sorted(trades, key=lambda t: (t.event_time, t.trade_id))

        # 使用 TestClock，初始时间为第一条 trade 的 event_time
        start_time = sorted_trades[0].event_time
        clock = TestClock(initial_ms=start_time)

        # 设置 confidence overrides
        if confidence_overrides:
            for symbol, conf in confidence_overrides.items():
                from src.domain import ConfidenceState
                self.state_machine.confidence._confidence[symbol] = ConfidenceState(conf)

        # 滚动窗口
        window = RollingWindow[TradeEvent](window_ms=self.window_ms)

        transitions: list[AnalysisEvent] = []
        snapshots: list[FeatureSnapshot] = []

        for trade in sorted_trades:
            clock.set(trade.receive_time)
            window.add(trade.receive_time, trade)

            # 获取窗口内 trades
            window_trades = window.get_items(trade.receive_time)

            # 计算 FeatureSnapshot
            snap = self.feature_engine.compute_snapshot(
                trade.symbol, window_trades, trade.receive_time,
            )

            # 状态机处理
            event = self.state_machine.process(snap, trade.receive_time)
            if event is not None:
                transitions.append(event)

        # 记录最终状态
        final_states = {
            sym: ssm.state.value
            for sym, ssm in self.state_machine._symbols.items()
        }

        return ReplayResult(
            events_processed=len(sorted_trades),
            transitions=transitions,
            snapshots=snapshots,
            final_states=final_states,
        )

    def replay_deterministic(
        self,
        trades: Sequence[TradeEvent],
    ) -> ReplayResult:
        """确定性重放 — 相同输入必须得到相同输出。

        连续重放两次，验证结果一致。
        """
        result1 = self.replay(trades)
        # 重置引擎
        self.feature_engine = FeatureEngine()
        self.state_machine = StateMachine()
        result2 = self.replay(trades)

        # 验证一致性
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
    """从 JSONL 文件加载 TradeEvent 序列。

    每行一个 JSON 对象，字段对应 Binance aggTrade payload。
    """
    from decimal import Decimal
    from src.domain import AggressorSide

    trades: list[TradeEvent] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            m = data.get("m")
            price = Decimal(str(data.get("p", "0")))
            qty = Decimal(str(data.get("q", "0")))
            trades.append(TradeEvent(
                symbol=data.get("s", ""),
                exchange="binance",
                trade_id=int(data.get("a", 0)),
                event_time=int(data.get("T", 0)),
                receive_time=int(data.get("T", 0)),
                price=price,
                qty=qty,
                quote_notional=price * qty,
                aggressor_side=AggressorSide.from_binance_m(m),
                is_maker=bool(m) if m is not None else False,
            ))
    return trades
