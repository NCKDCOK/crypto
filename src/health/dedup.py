"""Dedup 校验 — 确认 trade_id 去重在 Feature Engine 入口前生效。

依据：docs/DATA_HEALTH.md §6
trade_id 去重必须在进入 Feature Engine 之前完成，防止 CVD 被重复成交污染。

Gate 1 的 TradeDedup 已在 collector 出口实现去重。
本模块提供独立校验层，用于在 Feature Engine 入口做二次防线，
以及处理 depth 类 sequence 校验（未来）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.domain import TradeEvent

logger = logging.getLogger(__name__)


@dataclass
class DedupStats:
    """去重统计。"""

    total_seen: int = 0
    total_dropped: int = 0

    @property
    def drop_rate(self) -> float:
        if self.total_seen == 0:
            return 0.0
        return self.total_dropped / self.total_seen


class TradeDedupValidator:
    """trade_id 去重校验器 — Feature Engine 入口的二次防线。

    即使 collector 已去重，本层再次校验，防止：
    - 多个 collector 实例写入同一队列
    - replay 时事件重复
    - 边缘情况遗漏
    """

    def __init__(self) -> None:
        self._max_trade_ids: dict[str, int] = {}
        self.stats = DedupStats()

    def validate(self, event: TradeEvent) -> bool:
        """校验 trade 是否有效（trade_id > 已见最大值）。

        返回 True 表示有效（应继续处理），False 表示重复（应丢弃）。
        """
        self.stats.total_seen += 1
        max_seen = self._max_trade_ids.get(event.symbol)
        if max_seen is not None and event.trade_id <= max_seen:
            self.stats.total_dropped += 1
            logger.debug(
                "dedup_dropped symbol=%s trade_id=%d max_seen=%d",
                event.symbol,
                event.trade_id,
                max_seen,
            )
            return False
        self._max_trade_ids[event.symbol] = event.trade_id
        return True

    def get_max_trade_id(self, symbol: str) -> int | None:
        return self._max_trade_ids.get(symbol)

    def reset(self) -> None:
        """重置状态（如重连后）。"""
        self._max_trade_ids.clear()
        self.stats = DedupStats()
