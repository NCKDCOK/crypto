"""aggTrade Collector — WS 连接、订阅、解析、aggressor side、trade_id 去重。

依据：epic-01 Task 1.2
Binance aggTrade payload:
  e: "aggTrade"  E: event_time  s: symbol  a: trade_id
  p: price  q: qty  f: first_trade_id  l: last_trade_id
  T: trade_time  m: is_buyer_maker

P0 不变量：
- m=true → aggressor_side=SELL（买方是 maker → 卖方主动）
- trade_id 同 symbol 严格递增；重连后 ≤ 已见最大值则丢弃
- quote_notional = price × qty（本地计算）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from src.clock import Clock, SystemClock
from src.domain import AggressorSide, TradeEvent

from .base_ws import BaseWSCollector, WSStreamConfig

logger = logging.getLogger(__name__)


def parse_aggtrade_payload(payload: dict, receive_time: int) -> TradeEvent | None:
    """将 Binance aggTrade payload 解析为 TradeEvent。

    payload 字段：
        e: "aggTrade"  E: event_time  s: symbol  a: trade_id
        p: price  q: qty  T: trade_time  m: is_buyer_maker
    """
    if payload.get("e") != "aggTrade":
        return None

    symbol = payload.get("s", "")
    if not symbol:
        return None

    m = payload.get("m")  # 买方是否为 maker
    is_maker = bool(m) if m is not None else False

    price = Decimal(str(payload.get("p", "0")))
    qty = Decimal(str(payload.get("q", "0")))
    quote_notional = price * qty

    return TradeEvent(
        symbol=symbol,
        exchange="binance",
        trade_id=int(payload.get("a", 0)),
        event_time=int(payload.get("T", 0)),  # T = 成交时间
        receive_time=receive_time,
        price=price,
        qty=qty,
        quote_notional=quote_notional,
        aggressor_side=AggressorSide.from_binance_m(m),
        is_maker=is_maker,
    )


class TradeDedup:
    """trade_id 去重器 — 每个 symbol 独立维护已见最大 trade_id。

    重连后 trade_id ≤ 已见最大值则丢弃，防止 CVD 被重复成交污染。
    """

    def __init__(self) -> None:
        self._max_trade_ids: dict[str, int] = {}

    def should_accept(self, symbol: str, trade_id: int) -> bool:
        """判断该 trade 是否应该接受（trade_id > 已见最大值）。"""
        max_seen = self._max_trade_ids.get(symbol)
        if max_seen is not None and trade_id <= max_seen:
            return False
        self._max_trade_ids[symbol] = trade_id
        return True

    def get_max_trade_id(self, symbol: str) -> int | None:
        return self._max_trade_ids.get(symbol)


class AggTradeCollector(BaseWSCollector):
    """aggTrade WS collector。

    使用组合流订阅多个 symbol 的 aggTrade。
    例: streams = ["btcusdt@aggTrade", "ethusdt@aggTrade"]
    """

    def __init__(
        self,
        symbols: list[str],
        config: WSStreamConfig | None = None,
        clock: Clock | None = None,
        on_trade: Any = None,
    ) -> None:
        streams = [f"{s.lower()}@aggTrade" for s in symbols]
        cfg = config or WSStreamConfig(route="/market", streams=streams)
        cfg.streams = streams
        super().__init__(cfg, clock)
        self._dedup = TradeDedup()
        self._on_trade = on_trade

    def parse_payload(self, stream: str, payload: dict) -> TradeEvent | None:
        receive_time = self.clock.now_ms()
        return parse_aggtrade_payload(payload, receive_time)

    async def on_event(self, event: TradeEvent) -> None:
        """去重后回调。"""
        if not self._dedup.should_accept(event.symbol, event.trade_id):
            logger.debug(
                "aggtrade_dropped_duplicate symbol=%s trade_id=%d",
                event.symbol,
                event.trade_id,
            )
            return

        if self._on_trade:
            if callable(self._on_trade):
                result = self._on_trade(event)
                if hasattr(result, "__await__"):
                    await result
            else:
                await self._on_trade(event)

    @property
    def dedup(self) -> TradeDedup:
        """暴露 dedup 供测试。"""
        return self._dedup
