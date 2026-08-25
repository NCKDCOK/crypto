"""ConfidenceState 派生 — 从关键流 HealthLevel 派生置信上下文。

依据：docs/DATA_HEALTH.md §4, docs/DATA_MODEL.md §0.3

关键流（V1）：aggTrade、1m Kline、OI。
Funding/Premium 为非关键上下文流。

派生规则：
| 关键流状态 | ConfidenceState |
|------------|-----------------|
| 全部 OK | CONFIDENT |
| 存在 WARN，无 STALE/DRIFT/FAIL | DEGRADED |
| 任一关键流 STALE/DRIFT/FAIL | UNKNOWN |

对状态机的约束（Fail Closed）：
- CONFIDENT → 允许全部状态
- DEGRADED → 最高 SUSPECTED_START，禁止 CONFIRMED
- UNKNOWN → 禁止任何 CONFIRMED
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.domain import ConfidenceState, HealthLevel, HealthStatus

logger = logging.getLogger(__name__)

# V1 关键流前缀
CRITICAL_STREAM_PREFIXES: tuple[str, ...] = ("aggTrade", "kline", "oi_poller")


def is_critical_stream(stream: str) -> bool:
    """判断一个 stream 是否为关键流。"""
    return any(stream.startswith(prefix) for prefix in CRITICAL_STREAM_PREFIXES)


# 不健康等级（STALE / DRIFT / FAIL）
UNHEALTHY_LEVELS: frozenset[HealthLevel] = frozenset({
    HealthLevel.STALE,
    HealthLevel.DRIFT,
    HealthLevel.FAIL,
})


def derive_confidence(health_statuses: list[HealthStatus]) -> ConfidenceState:
    """从关键流 HealthLevel 派生 ConfidenceState。

    Args:
        health_statuses: 所有 stream 的 HealthStatus 列表。

    Returns:
        ConfidenceState:
        - 全部关键流 OK → CONFIDENT
        - 存在 WARN 但无 STALE/DRIFT/FAIL → DEGRADED
        - 任一关键流 STALE/DRIFT/FAIL → UNKNOWN
    """
    critical = [hs for hs in health_statuses if is_critical_stream(hs.stream)]

    if not critical:
        # 没有关键流注册 — 保守 UNKNOWN
        return ConfidenceState.UNKNOWN

    # 检查是否有任一关键流不健康
    has_unhealthy = any(hs.status in UNHEALTHY_LEVELS for hs in critical)
    if has_unhealthy:
        unhealthy = [hs for hs in critical if hs.status in UNHEALTHY_LEVELS]
        for hs in unhealthy:
            logger.warning(
                "confidence_degraded_to_unknown stream=%s status=%s reason=%s",
                hs.stream,
                hs.status,
                hs.reason,
            )
        return ConfidenceState.UNKNOWN

    # 检查是否有 WARN
    has_warn = any(hs.status == HealthLevel.WARN for hs in critical)
    if has_warn:
        return ConfidenceState.DEGRADED

    # 全部 OK
    return ConfidenceState.CONFIDENT


def can_confirm(confidence: ConfidenceState) -> bool:
    """ConfidenceState 是否允许进入 CONFIRMED 状态。"""
    return confidence == ConfidenceState.CONFIDENT


def can_suspect(confidence: ConfidenceState) -> bool:
    """ConfidenceState 是否允许进入 SUSPECTED_START 状态。"""
    return confidence in (ConfidenceState.CONFIDENT, ConfidenceState.DEGRADED)


@dataclass
class ConfidenceTracker:
    """ConfidenceState 追踪器 — 集中维护各 symbol 的 confidence。

    Feature Engine 和 Detector 通过本追踪器获取 confidence_state，
    据此 fail closed。
    """

    _confidence: dict[str, ConfidenceState] = field(default_factory=dict)

    def update(self, symbol: str, health_statuses: list[HealthStatus]) -> ConfidenceState:
        """更新某 symbol 的 confidence_state。"""
        # 过滤出该 symbol 的关键流
        symbol_statuses = [
            hs for hs in health_statuses
            if hs.symbol == symbol or hs.symbol is None
        ]
        confidence = derive_confidence(symbol_statuses)
        old = self._confidence.get(symbol)
        self._confidence[symbol] = confidence
        if old != confidence:
            logger.info(
                "confidence_changed symbol=%s old=%s new=%s",
                symbol,
                old,
                confidence,
            )
        return confidence

    def get(self, symbol: str) -> ConfidenceState:
        """获取某 symbol 的 confidence_state。默认 UNKNOWN。"""
        return self._confidence.get(symbol, ConfidenceState.UNKNOWN)

    def can_confirm(self, symbol: str) -> bool:
        return can_confirm(self.get(symbol))

    def can_suspect(self, symbol: str) -> bool:
        return can_suspect(self.get(symbol))
