"""Long/Short Ratio Collector — REST 定时获取三个多空比指标（V1.4 §二十三）。

依据：crypto_radar_v1.4_fix_update_plan.md §二十三 — 必须严格区分三个指标，
禁止混为同一个 long_short_ratio。

Binance REST（/futures/data/*，5m 周期）：
    globalLongShortAccountRatio → global_account_ls_ratio     普通账户多空比
    topLongShortAccountRatio    → top_trader_account_ls_ratio  大户账户多空比
    topLongShortPositionRatio   → top_trader_position_ls_ratio 大户持仓多空比

每个端点返回 [{symbol, longShortRatio, longAccount, shortAccount, timestamp}, ...]，
ratio = longAccount / shortAccount（>1 偏多，<1 偏空）。

仅作 setup evidence / short crowding 输入，不单独触发信号（§十五/§二十四）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.clock import Clock, SystemClock
from src.domain import LongShortRatioSnapshot
from src.health.rate_limiter import CircuitOpenError, IPBannedError, RateLimiter

logger = logging.getLogger(__name__)

# 端点 → snapshot 字段
_ENDPOINT_FIELD: dict[str, str] = {
    "globalLongShortAccountRatio": "global_account_ls_ratio",
    "topLongShortAccountRatio": "top_trader_account_ls_ratio",
    "topLongShortPositionRatio": "top_trader_position_ls_ratio",
}


def parse_long_short_ratio_response(
    data: list[dict] | dict,
    field: str,
    receive_time: int,
) -> float | None:
    """解析单个 /futures/data/*LongShort*Ratio 端点响应 → ratio。

    响应为 list（取最新一条）；取 longShortRatio（已为 long/short）。
    """
    if isinstance(data, list):
        if not data:
            return None
        item = data[-1]
    elif isinstance(data, dict):
        item = data
    else:
        return None
    try:
        return float(item.get("longShortRatio"))
    except (TypeError, ValueError):
        return None


class LongShortRatioCollector:
    """三个多空比指标定时采集器（§二十三）。

    定期对每个 symbol 轮询三个 /futures/data 端点，合并为一条
    LongShortRatioSnapshot。RateLimiter 控速；单 symbol/端点失败不阻塞其他。
    """

    def __init__(
        self,
        symbols: list[str],
        rate_limiter: RateLimiter,
        base_url: str = "https://fapi.binance.com",
        period: str = "5m",
        poll_interval_s: float = 180.0,
        clock: Clock | None = None,
        on_snapshot: Any = None,
    ) -> None:
        self.symbols = list(symbols)
        self.rate_limiter = rate_limiter
        self.base_url = base_url
        self.period = period
        self.poll_interval_s = poll_interval_s
        self.clock = clock or SystemClock()
        self._on_snapshot = on_snapshot
        self._running = False
        self._task: asyncio.Task | None = None

    async def poll_one(self, symbol: str) -> LongShortRatioSnapshot | None:
        """轮询单 symbol 的三个多空比端点，合并为一条快照。"""
        fields: dict[str, float | None] = {v: None for v in _ENDPOINT_FIELD.values()}
        receive_time = self.clock.now_ms()
        event_time = receive_time
        for endpoint, field in _ENDPOINT_FIELD.items():
            url = f"{self.base_url}/futures/data/{endpoint}"
            try:
                resp = await self.rate_limiter.request(
                    "GET", url,
                    params={"symbol": symbol, "period": self.period},
                )
                if resp.status_code != 200:
                    logger.warning(
                        "ls_ratio_poll_error symbol=%s endpoint=%s status=%d",
                        symbol, endpoint, resp.status_code,
                    )
                    continue
                data = resp.json()
                ratio = parse_long_short_ratio_response(data, field, receive_time)
                if ratio is not None:
                    fields[field] = ratio
                    # 取响应内最新 timestamp 作为 event_time
                    item = data[-1] if isinstance(data, list) and data else data
                    if isinstance(item, dict):
                        try:
                            event_time = max(event_time, int(item.get("timestamp", event_time)))
                        except (TypeError, ValueError):
                            pass
            except (CircuitOpenError, IPBannedError) as e:
                logger.warning("ls_ratio_rate_limited symbol=%s endpoint=%s error=%s",
                               symbol, endpoint, e)
            except Exception as e:
                logger.warning("ls_ratio_poll_error symbol=%s endpoint=%s error=%s",
                               symbol, endpoint, e)

        if all(v is None for v in fields.values()):
            return None
        snap = LongShortRatioSnapshot(
            symbol=symbol,
            event_time=event_time,
            receive_time=receive_time,
            global_account_ls_ratio=fields["global_account_ls_ratio"],
            top_trader_account_ls_ratio=fields["top_trader_account_ls_ratio"],
            top_trader_position_ls_ratio=fields["top_trader_position_ls_ratio"],
        )
        if self._on_snapshot:
            result = self._on_snapshot(snap)
            if hasattr(result, "__await__"):
                await result
        return snap

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

    def add_symbol(self, symbol: str) -> None:
        if symbol not in self.symbols:
            self.symbols.append(symbol)

    def remove_symbol(self, symbol: str) -> None:
        if symbol in self.symbols:
            self.symbols.remove(symbol)
