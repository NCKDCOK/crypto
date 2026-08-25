"""Replay Calibration — Setup 快照 + 未来表现（V1.2 §42）。

每个正式 Setup 记录：
symbol / setup_type / state / market_regime / feature_snapshot / subscores /
opportunity / signal_confirmation / data_confidence / entry_zone / invalidation /
tp1/tp2/tp3 / future_5m / future_15m / future_1h / MFE / MAE

后续统计：
  吸筹 > 80 → 2h 内启动概率
  启动质量 > 80 → 15m 正向概率
  机会分 > 85 → 1h MFE/MAE
  派发风险 > 70 → 未来 15m 回撤概率

注：历史同类胜率（historical_success_rate）需足够样本后才输出（§4）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SetupSnapshot:
    """Setup 校准快照。"""

    symbol: str
    asof: int
    setup_type: str
    state: str
    market_regime: str | None = None
    direction: str | None = None
    feature_snapshot: dict[str, Any] = field(default_factory=dict)
    subscores: dict[str, Any] = field(default_factory=dict)
    opportunity: float | None = None
    signal_confirmation: float | None = None
    data_confidence: float | None = None
    entry_zone: tuple[float | None, float | None] | None = None
    invalidation: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    # 未来表现（回填）
    future_5m: float | None = None
    future_15m: float | None = None
    future_1h: float | None = None
    mfe: float | None = None  # Maximum Favorable Excursion
    mae: float | None = None  # Maximum Adverse Excursion

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "asof": self.asof, "setup_type": self.setup_type,
            "state": self.state, "market_regime": self.market_regime, "direction": self.direction,
            "feature_snapshot": self.feature_snapshot, "subscores": self.subscores,
            "opportunity": self.opportunity, "signal_confirmation": self.signal_confirmation,
            "data_confidence": self.data_confidence,
            "entry_zone": list(self.entry_zone) if self.entry_zone else None,
            "invalidation": self.invalidation, "tp1": self.tp1, "tp2": self.tp2, "tp3": self.tp3,
            "future_5m": self.future_5m, "future_15m": self.future_15m, "future_1h": self.future_1h,
            "mfe": self.mfe, "mae": self.mae,
        }


class CalibrationStore:
    """校准快照存储 — 内存 + JSON 持久化。"""

    def __init__(self) -> None:
        self._snapshots: list[SetupSnapshot] = []

    def add(self, snap: SetupSnapshot) -> None:
        self._snapshots.append(snap)

    def get_all(self) -> list[SetupSnapshot]:
        return list(self._snapshots)

    def get_by_setup(self, setup_type: str) -> list[SetupSnapshot]:
        return [s for s in self._snapshots if s.setup_type == setup_type]

    def backfill_future(self, symbol: str, asof: int, future_5m: float, future_15m: float,
                        future_1h: float, mfe: float, mae: float) -> int:
        """回填未来表现到匹配的快照。返回回填数量。"""
        count = 0
        for s in self._snapshots:
            if s.symbol == symbol and s.asof == asof and s.future_5m is None:
                s.future_5m = future_5m
                s.future_15m = future_15m
                s.future_1h = future_1h
                s.mfe = mfe
                s.mae = mae
                count += 1
        return count

    def stats_by_bucket(self, metric: str, threshold: float, future_field: str = "future_15m") -> dict[str, Any]:
        """统计分桶正向率。

        Args:
            metric: opportunity / accumulation / startup_quality / distribution_risk
            threshold: 阈值（如 80）
            future_field: future_5m / future_15m / future_1h
        """
        matching = []
        for s in self._snapshots:
            val = None
            if metric == "opportunity":
                val = s.opportunity
            elif metric in s.subscores:
                ss = s.subscores[metric]
                val = ss.get("score") if isinstance(ss, dict) else None
            elif metric == "distribution_risk":
                val = s.subscores.get("distribution_risk", {}).get("score") if s.subscores else None
            if val is not None and val > threshold:
                future = getattr(s, future_field, None)
                if future is not None:
                    matching.append(future)
        if not matching:
            return {"sample": 0, "positive_rate": None}
        positive = sum(1 for f in matching if f > 0)
        return {
            "sample": len(matching),
            "positive_rate": round(positive / len(matching), 3),
            "avg": round(sum(matching) / len(matching), 4),
        }

    def save_to_json(self, path: Path) -> None:
        data = [s.to_dict() for s in self._snapshots]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_from_json(self, path: Path) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            ez = item.get("entry_zone")
            self.add(SetupSnapshot(
                symbol=item["symbol"], asof=item["asof"], setup_type=item["setup_type"],
                state=item["state"], market_regime=item.get("market_regime"),
                direction=item.get("direction"), feature_snapshot=item.get("feature_snapshot", {}),
                subscores=item.get("subscores", {}), opportunity=item.get("opportunity"),
                signal_confirmation=item.get("signal_confirmation"),
                data_confidence=item.get("data_confidence"),
                entry_zone=tuple(ez) if ez else None, invalidation=item.get("invalidation"),
                tp1=item.get("tp1"), tp2=item.get("tp2"), tp3=item.get("tp3"),
                future_5m=item.get("future_5m"), future_15m=item.get("future_15m"),
                future_1h=item.get("future_1h"), mfe=item.get("mfe"), mae=item.get("mae"),
            ))
