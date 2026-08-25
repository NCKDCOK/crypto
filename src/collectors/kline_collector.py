"""Kline Collector — 1m closed kline + 必要实时 kline。

依据：epic-01 Task 1.3
Binance kline payload:
  e: "kline"  E: event_time  s: symbol
  k: { t: open_time, T: close_time, s: symbol, i: interval,
       o: open, c: close, h: high, l: low, v: volume, n: trade_count,
       x: is_closed, q: quote_volume }

P0 不变量：仅 is_closed=true 的 bar 可进入慢周期确认。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.clock import Clock, SystemClock
from src.domain import KlineEvent, KlineInterval

from .base_ws import BaseWSCollector, WSStreamConfig

logger = logging.getLogger(__name__)


def parse_kline_payload(payload: dict, receive_time: int) -> KlineEvent | None:
    """将 Binance kline payload 解析为 KlineEvent。

    payload 中 k 对象含 OHLCV + is_closed。
    """
    if payload.get("e") != "kline":
        return None

    k = payload.get("k")
    if not k or not isinstance(k, dict):
        return None

    symbol = k.get("s", "")
    if not symbol:
        return None

    interval_str = k.get("i", "1m")
    try:
        interval = KlineInterval(interval_str)
    except ValueError:
        logger.warning("kline_unknown_interval interval=%s", interval_str)
        return None

    return KlineEvent(
        symbol=symbol,
        exchange="binance",
        interval=interval,
        open_time=int(k.get("t", 0)),
        close_time=int(k.get("T", 0)),
        event_time=int(payload.get("E", 0)),
        receive_time=receive_time,
        open=Decimal(str(k.get("o", "0"))),
        high=Decimal(str(k.get("h", "0"))),
        low=Decimal(str(k.get("l", "0"))),
        close=Decimal(str(k.get("c", "0"))),
        volume=Decimal(str(k.get("v", "0"))),
        quote_volume=Decimal(str(k.get("q", "0"))) if k.get("q") else None,
        trade_count=int(k.get("n", 0)),
        is_closed=bool(k.get("x", False)),
    )


class KlineCollector(BaseWSCollector):
    """Kline WS collector。

    使用组合流订阅多个 symbol 的 kline。
    例: streams = ["btcusdt@kline_1m", "ethusdt@kline_1m"]
    """

    def __init__(
        self,
        symbols: list[str],
        interval: KlineInterval = KlineInterval.M1,
        config: WSStreamConfig | None = None,
        clock: Clock | None = None,
        on_kline: Any = None,
    ) -> None:
        streams = [f"{s.lower()}@kline_{interval.value}" for s in symbols]
        cfg = config or WSStreamConfig(route="/market", streams=streams)
        cfg.streams = streams
        super().__init__(cfg, clock)
        self._on_kline = on_kline

    def parse_payload(self, stream: str, payload: dict) -> KlineEvent | None:
        receive_time = self.clock.now_ms()
        return parse_kline_payload(payload, receive_time)

    async def on_event(self, event: KlineEvent) -> None:
        if self._on_kline:
            if callable(self._on_kline):
                result = self._on_kline(event)
                if hasattr(result, "__await__"):
                    await result
            else:
                await self._on_kline(event)
