"""Funding/Premium Collector — REST 定时获取资金费率和标记价格。

依据：epic-01 Task 1.5
Binance REST: GET /fapi/v1/premiumIndex?symbol=<SYMBOL>
响应字段：
    symbol, markPrice, indexPrice, estimatedSettlePrice,
    lastFundingRate, nextFundingTime, time

premium = markPrice - indexPrice（本地计算）
仅作 context / soft veto，不单独触发信号。
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from src.clock import Clock, SystemClock
from src.domain import FundingRateSnapshot
from src.health.rate_limiter import CircuitOpenError, IPBannedError, RateLimiter

logger = logging.getLogger(__name__)


def parse_premium_index_response(
    data: dict,
    symbol: str,
    receive_time: int,
) -> FundingRateSnapshot | None:
    """解析 Binance /fapi/v1/premiumIndex 响应。

    响应字段：
        symbol, markPrice, indexPrice, lastFundingRate, nextFundingTime, time
    premium = markPrice - indexPrice（本地计算）
    """
    if not data or "markPrice" not in data:
        return None

    mark_price = Decimal(str(data.get("markPrice", "0")))
    index_price = Decimal(str(data.get("indexPrice", "0")))
    premium = mark_price - index_price

    event_time = int(data.get("time", receive_time))

    return FundingRateSnapshot(
        symbol=symbol,
        exchange="binance",
        event_time=event_time,
        receive_time=receive_time,
        mark_price=mark_price,
        index_price=index_price,
        last_funding_rate=Decimal(str(data.get("lastFundingRate", "0"))),
        next_funding_time=int(data.get("nextFundingTime", 0)),
        premium=premium,
        source="binance_rest_premiumindex",
    )


class FundingPremiumCollector:
    """Funding/Premium 定时采集器。

    定期对每个 symbol 调用 premiumIndex REST。
    通过 RateLimiter 控速。单 symbol 失败不阻塞其他。
    """

    def __init__(
        self,
        symbols: list[str],
        rate_limiter: RateLimiter,
        base_url: str = "https://fapi.binance.com",
        poll_interval_s: float = 30.0,
        clock: Clock | None = None,
        on_snapshot: Any = None,
    ) -> None:
        self.symbols = list(symbols)
        self.rate_limiter = rate_limiter
        self.base_url = base_url
        self.poll_interval_s = poll_interval_s
        self.clock = clock or SystemClock()
        self._on_snapshot = on_snapshot
        self._running = False
        self._task: asyncio.Task | None = None

    async def poll_one(self, symbol: str) -> FundingRateSnapshot | None:
        """轮询单个 symbol 的 funding/premium。失败返回 None。"""
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        try:
            resp = await self.rate_limiter.request("GET", url, params={"symbol": symbol})
            if resp.status_code != 200:
                logger.warning(
                    "funding_poll_error symbol=%s status=%d", symbol, resp.status_code
                )
                return None
            receive_time = self.clock.now_ms()
            data = resp.json()
            snap = parse_premium_index_response(data, symbol, receive_time)
            if snap and self._on_snapshot:
                if callable(self._on_snapshot):
                    result = self._on_snapshot(snap)
                    if hasattr(result, "__await__"):
                        await result
                else:
                    await self._on_snapshot(snap)
            return snap
        except (CircuitOpenError, IPBannedError) as e:
            logger.warning("funding_poll_rate_limited symbol=%s error=%s", symbol, e)
            return None
        except Exception as e:
            logger.warning("funding_poll_error symbol=%s error=%s", symbol, e)
            return None

    async def _poll_cycle(self) -> None:
        for symbol in self.symbols:
            if not self._running:
                break
            await self.poll_one(symbol)

    async def _poll_loop(self) -> None:
        while self._running:
            await self._poll_cycle()
            await asyncio.sleep(self.poll_interval_s)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
