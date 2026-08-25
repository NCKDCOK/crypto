"""Labeling — 人工标注接口。

依据：SYSTEM_DESIGN.md §12
标注类别：false_start / continuation / squeeze_only / absorption / withdrawal
标签只记录，不回写改变状态机。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class LabelType(str, Enum):
    """标注类别。"""

    FALSE_START = "false_start"
    CONTINUATION = "continuation"
    SQUEEZE_ONLY = "squeeze_only"
    ABSORPTION = "absorption"
    WITHDRAWAL = "withdrawal"
    CLEAN_START = "clean_start"


@dataclass
class Label:
    """单个标注。"""

    symbol: str
    asof: int
    label_type: LabelType
    annotator: str = "manual"
    notes: str = ""
    # 后续表现
    outcome: dict[str, Any] = field(default_factory=dict)


class LabelStore:
    """标注存储 — 内存实现。"""

    def __init__(self) -> None:
        self._labels: list[Label] = []

    def add(self, label: Label) -> None:
        self._labels.append(label)

    def get_by_symbol(self, symbol: str) -> list[Label]:
        return [l for l in self._labels if l.symbol == symbol]

    def get_all(self) -> list[Label]:
        return list(self._labels)

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for l in self._labels:
            key = l.label_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def save_to_json(self, path: Path) -> None:
        data = [
            {
                "symbol": l.symbol,
                "asof": l.asof,
                "label_type": l.label_type.value,
                "annotator": l.annotator,
                "notes": l.notes,
                "outcome": l.outcome,
            }
            for l in self._labels
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_from_json(self, path: Path) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            self.add(Label(
                symbol=item["symbol"],
                asof=item["asof"],
                label_type=LabelType(item["label_type"]),
                annotator=item.get("annotator", "manual"),
                notes=item.get("notes", ""),
                outcome=item.get("outcome", {}),
            ))
