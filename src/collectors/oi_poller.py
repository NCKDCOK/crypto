"""OI Poller — REST 定时快照、统一时间戳、per-symbol 隔离。

依据：epic-01 Task 1.4
Binance REST: GET /fapi/v1/openInterest?symbol=<SYMBOL>
响应: { "openInterest": "100.5", "symbol": "BTCUSDT", "time": 1672515782136 }

P0 不变量：
- open_interest 单位 = 基础资产数量（非美元名义）
- 速率受控（通过 RateLimiter）
- 单 symbol 错误不阻塞其他 symbol
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from src.clock import Clock, SystemClock
from src.domain import OpenInterestSnapshot
from src.health.rate_limiter import CircuitOpenError, IPBannedError, RateLimiter

logger = logging.getLogger(__name__)


def parse_open_interest_response(
    data: dict,
    symbol: str,
    receive_time: int,
) -> OpenInterestSnapshot | None:
    """解析 Binance /fapi/v1/openInterest 响应。

    响应字段：
        openInterest: 基础资产数量
        symbol: 交易对
        time: 撮合引擎时间
    """
    if not data or "openInterest" not in data:
        return None

    event_time = int(data.get("time", receive_time))

    return OpenInterestSnapshot(
        symbol=symbol,
        exchange="binance",
        event_time=event_time,
        receive_time=receive_time,
        open_interest=Decimal(str(data["openInterest"])),
        source="binance_rest_openinterest",
        freshness_ms=0,  # 刚收到，age=0
    )


class OIPoller:
    """OI 定时轮询器。

    定期对每个 symbol 调用 openInterest REST，解析为 OpenInterestSnapshot。
    通过 RateLimiter 控速。单 symbol 失败不阻塞其他。
    """

    def __init__(
        self,
        symbols: list[str],
        rate_limiter: RateLimiter,
        base_url: str = "https://fapi.binance.com",
        poll_interval_s: float = 5.0,
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

    async def poll_one(self, symbol: str) -> OpenInterestSnapshot | None:
        """轮询单个 symbol 的 OI。失败返回 None，不抛异常。"""
        url = f"{self.base_url}/fapi/v1/openInterest"
        try:
            resp = await self.rate_limiter.request("GET", url, params={"symbol": symbol})
            if resp.status_code != 200:
                logger.warning(
                    "oi_poll_error symbol=%s status=%d", symbol, resp.status_code
                )
                return None
            receive_time = self.clock.now_ms()
            data = resp.json()
            snap = parse_open_interest_response(data, symbol, receive_time)
            if snap and self._on_snapshot:
                if callable(self._on_snapshot):
                    result = self._on_snapshot(snap)
                    if hasattr(result, "__await__"):
                        await result
                else:
                    await self._on_snapshot(snap)
            return snap
        except (CircuitOpenError, IPBannedError) as e:
            logger.warning("oi_poll_rate_limited symbol=%s error=%s", symbol, e)
            return None
        except Exception as e:
            logger.warning("oi_poll_error symbol=%s error=%s", symbol, e)
            return None

    async def _poll_cycle(self) -> None:
        """单轮轮询所有 symbol。单 symbol 失败不阻塞其他。"""
        for symbol in self.symbols:
            if not self._running:
                break
            await self.poll_one(symbol)

    async def _poll_loop(self) -> None:
        """持续轮询循环。"""
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
