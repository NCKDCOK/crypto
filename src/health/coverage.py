"""Data Health 覆盖率计算 — V1.3 §46。

覆盖率 = 健康（OK/WARN）的 symbol×stream 配对占全部注册配对的比例
（而不是"某一个币 OI 延迟 → 整个首页显示数据异常"的旧行为）。

分级（配置 HealthCoverageConfig，默认 ok_min=90，degraded_min=70）：

- coverage_pct >= ok_min        → normal（正常）
- coverage_pct >= degraded_min  → degraded（部分降级）
- coverage_pct <  degraded_min  → anomaly（异常）
- 核心数据源整体断线             → critical（严重异常），无论覆盖率

核心数据源整体断线：存在 >= 1 个核心流配对（stream 名以 critical_stream_prefix
开头，默认 aggTrade），且所有核心流配对均不健康（非 OK/WARN）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from src.domain import HealthLevel

# 健康 = OK 或 WARN（WARN=预热中视为健康但未完全就绪）
HEALTHY_LEVELS = frozenset({HealthLevel.OK, HealthLevel.WARN})

LEVEL_NORMAL = "normal"
LEVEL_DEGRADED = "degraded"
LEVEL_ANOMALY = "anomaly"
LEVEL_CRITICAL = "critical"

LEVEL_LABELS = {
    LEVEL_NORMAL: "正常",
    LEVEL_DEGRADED: "部分降级",
    LEVEL_ANOMALY: "异常",
    LEVEL_CRITICAL: "严重异常",
}

# 输入配对外形：(stream_name, level)。stream_name 可为 ""（不参与 per-stream 分组），
# level 为 HealthLevel 枚举值或等价字符串。stream_name 可带 symbol 后缀
# （如 "aggTrade:BTCUSDT"），按第一个 ":" 前的部分分组。
Pair = tuple[str, str | HealthLevel]


def _level_of(value: str | HealthLevel) -> HealthLevel:
    if isinstance(value, HealthLevel):
        return value
    return HealthLevel(value)


def _is_healthy(value: str | HealthLevel) -> bool:
    return _level_of(value) in HEALTHY_LEVELS


@dataclass
class CoverageResult:
    """覆盖率计算结果（immutable 语义：直接构造函数 + to_dict）。"""

    coverage_pct: float
    healthy_pairs: int
    total_pairs: int
    level: str
    level_label: str
    critical_stream_down: bool
    per_stream: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "coverage_pct": self.coverage_pct,
            "healthy_pairs": self.healthy_pairs,
            "total_pairs": self.total_pairs,
            "level": self.level,
            "level_label": self.level_label,
            "critical_stream_down": self.critical_stream_down,
            "per_stream": dict(self.per_stream),
        }


def compute_coverage(
    pairs: Sequence[Pair] | Iterable[Pair],
    *,
    ok_min: float = 90.0,
    degraded_min: float = 70.0,
    critical_stream_prefix: str = "aggTrade",
) -> CoverageResult:
    """按 §46 规则计算覆盖率。

    - 无任何配对（total == 0）→ coverage_pct=100.0、level=normal（空集不报异常）。
    - 核心流存在且全部不健康 → critical（严重异常），优先级最高。
    - per_stream：按 stream 名前缀（":" 之前）分组，值 = 该组健康占比 × 100。
      仅统计带名字的配对。
    """
    items = list(pairs)
    total = len(items)
    if total == 0:
        return CoverageResult(
            coverage_pct=100.0,
            healthy_pairs=0,
            total_pairs=0,
            level=LEVEL_NORMAL,
            level_label=LEVEL_LABELS[LEVEL_NORMAL],
            critical_stream_down=False,
            per_stream={},
        )

    healthy = sum(1 for _, level in items if _is_healthy(level))
    coverage_pct = round(100.0 * healthy / total, 1)

    # 核心数据源整体断线判定：存在核心流配对且全部不健康
    core_pairs = [p for p in items if p[0].startswith(critical_stream_prefix)]
    critical_down = bool(core_pairs) and all(not _is_healthy(level) for _, level in core_pairs)

    if critical_down:
        level = LEVEL_CRITICAL
    elif coverage_pct >= ok_min:
        level = LEVEL_NORMAL
    elif coverage_pct >= degraded_min:
        level = LEVEL_DEGRADED
    else:
        level = LEVEL_ANOMALY

    # per-stream 分组
    per_stream: dict[str, float] = {}
    groups: dict[str, list[Pair]] = {}
    for pair in items:
        name = pair[0]
        if not name:
            continue
        prefix = name.split(":", 1)[0]
        groups.setdefault(prefix, []).append(pair)
    for prefix, group in groups.items():
        per_stream[prefix] = round(
            100.0 * sum(1 for _, level in group if _is_healthy(level)) / len(group), 1
        )

    return CoverageResult(
        coverage_pct=coverage_pct,
        healthy_pairs=healthy,
        total_pairs=total,
        level=level,
        level_label=LEVEL_LABELS[level],
        critical_stream_down=critical_down,
        per_stream=per_stream,
    )