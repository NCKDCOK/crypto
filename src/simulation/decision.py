"""DecisionSnapshotService — §11 稳定决策快照。

依据：V1.3 更新计划 §11 / §12 / §54 / §55 / §56。

首页不再直接展示“实时 Engine 当前值”，而是展示冻结于
decision_snapshot_s 周期（RankingConfig，默认 30s）的“稳定决策快照”。
- §54 分数趋势箭头：对比的是 30s/1m 平滑窗口，不是下一秒比较 → 快照天然提供
  稳定基准。
- §55/§56：快照值 vs 当前值 双显示（快照来自本服务，当前值来自实时 SymbolRuntimeState）。

后台仍然 1~2 秒实时计算（§12），本服务只负责“展示层冻结”，不影响引擎。
"""

from __future__ import annotations

from typing import Any


class DecisionSnapshotService:
    """按 symbol 冻结决策快照；周期内重复 update 返回旧快照（稳定）。"""

    def __init__(self, interval_s: float = 30.0) -> None:
        self.interval_ms: int = max(int(interval_s * 1000.0), 1)
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._frozen_at: dict[str, int] = {}

    def update(self, symbol: str, now_ms: int, decision: dict[str, Any]) -> dict[str, Any]:
        """冻结/返回该 symbol 的稳定决策快照。

        距上次冻结 >= interval_ms 才更新快照；否则返回上一次的冻结值。
        返回 {frozen_at, decision}（副本，调用方修改不污染内部状态）。
        """
        last = self._frozen_at.get(symbol)
        if last is None or now_ms - last >= self.interval_ms:
            self._snapshots[symbol] = {
                "frozen_at": now_ms,
                "decision": dict(decision),
            }
            self._frozen_at[symbol] = now_ms
        snap = self._snapshots[symbol]
        return {"frozen_at": snap["frozen_at"], "decision": dict(snap["decision"])}

    def get(self, symbol: str) -> dict[str, Any] | None:
        """返回 {frozen_at, decision}；无快照返回 None。"""
        snap = self._snapshots.get(symbol)
        if snap is None:
            return None
        return {"frozen_at": snap["frozen_at"], "decision": dict(snap["decision"])}

    def all(self) -> dict[str, dict[str, Any]]:
        return {k: {"frozen_at": v["frozen_at"], "decision": dict(v["decision"])}
                for k, v in self._snapshots.items()}

    def restore(self, symbol: str, frozen_at: int, snapshot: dict[str, Any]) -> None:
        """§48 重启恢复：重新载入已冻结快照。"""
        self._snapshots[symbol] = {"frozen_at": frozen_at, "decision": dict(snapshot)}
        self._frozen_at[symbol] = frozen_at