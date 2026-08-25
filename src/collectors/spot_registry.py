"""Spot Symbol Registry — Binance 现货交易对发现（V1.2 §9）。

从现货 exchangeInfo 发现可用现货对，提供 perp→spot 匹配。
无现货市场的 perp → spot 不可用（标记 unavailable，不伪造）。

Binance Spot REST: GET https://api.binance.com/api/v3/exchangeInfo
spot symbols 字段：symbol, status, baseAsset, quoteAsset
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SpotSymbolRegistry:
    """现货交易对注册表。"""

    quote_asset: str = "USDT"
    _symbols: set[str] = field(default_factory=set, init=False, repr=False)

    @property
    def symbols(self) -> set[str]:
        return set(self._symbols)

    def update_from_exchange_info(self, data: dict) -> set[str]:
        """从现货 exchangeInfo 更新可用 symbol 集合。

        spot symbols 字段：symbol, status, baseAsset, quoteAsset
        """
        self._symbols.clear()
        for item in data.get("symbols", []):
            if item.get("status") != "TRADING":
                continue
            if item.get("quoteAsset") != self.quote_asset:
                continue
            sym = item.get("symbol", "")
            if sym:
                self._symbols.add(sym)
        logger.info("spot_registry_updated spot_symbols=%d", len(self._symbols))
        return set(self._symbols)

    def has_spot(self, symbol: str) -> bool:
        """该 symbol 是否有对应现货市场（perp symbol 通常与 spot 同名）。"""
        return symbol in self._symbols

    def perp_to_spot(self, symbol: str) -> str | None:
        """perp → spot 映射。默认同名；无现货返回 None。"""
        return symbol if symbol in self._symbols else None

    async def fetch_from_api(
        self,
        base_url: str = "https://api.binance.com",
        client: httpx.AsyncClient | None = None,
        proxy: str | None = None,
    ) -> set[str]:
        """从 Binance 现货 REST 获取 exchangeInfo 并更新注册表。"""
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=15, proxy=proxy)
        try:
            resp = await client.get(f"{base_url}/api/v3/exchangeInfo")
            resp.raise_for_status()
            data = resp.json()
            return self.update_from_exchange_info(data)
        except Exception:
            logger.exception("spot_registry_fetch_failed")
            return set(self._symbols)
        finally:
            if own_client:
                await client.aclose()
