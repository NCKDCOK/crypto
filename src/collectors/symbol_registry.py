"""Symbol Registry — USDT-M 交易对发现、过滤下架/无效 symbol。

依据：epic-01 Task 1.1
Binance REST: GET /fapi/v1/exchangeInfo
只采集与标准化，不分析。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SymbolInfo:
    """单个交易对的元信息。"""

    symbol: str
    pair: str
    contract_type: str  # PERPETUAL / CURRENT_MONTH / ...
    status: str  # TRADING / PENDING_TRADING / DELISTED / ...
    base_asset: str
    quote_asset: str
    onboard_date: int | None = None


@dataclass
class SymbolRegistry:
    """USDT-M 交易对注册表。

    从 Binance exchangeInfo 发现所有 symbol，过滤出：
    - contract_type == PERPETUAL
    - status == TRADING
    - quote_asset 匹配（默认 USDT）
    - 不匹配排除模式
    """

    quote_asset: str = "USDT"
    exclude_patterns: list[str] = field(default_factory=list)
    _symbols: dict[str, SymbolInfo] = field(default_factory=dict, init=False, repr=False)

    @property
    def symbols(self) -> dict[str, SymbolInfo]:
        return dict(self._symbols)

    def filter_symbol(self, info: SymbolInfo) -> bool:
        """判断一个 symbol 是否应该被纳入。"""
        if info.contract_type != "PERPETUAL":
            return False
        if info.status != "TRADING":
            return False
        if info.quote_asset != self.quote_asset:
            return False
        for pattern in self.exclude_patterns:
            if pattern in info.symbol:
                return False
        return True

    def parse_exchange_info(self, data: dict) -> list[SymbolInfo]:
        """解析 Binance exchangeInfo 响应。

        响应中 symbols 数组每个元素含：
        symbol, pair, contractType, status, baseAsset, quoteAsset, onboardDate
        """
        result: list[SymbolInfo] = []
        for item in data.get("symbols", []):
            info = SymbolInfo(
                symbol=item.get("symbol", ""),
                pair=item.get("pair", ""),
                contract_type=item.get("contractType", ""),
                status=item.get("status", ""),
                base_asset=item.get("baseAsset", ""),
                quote_asset=item.get("quoteAsset", ""),
                onboard_date=item.get("onboardDate"),
            )
            result.append(info)
        return result

    def update_from_exchange_info(self, data: dict) -> list[str]:
        """从 exchangeInfo 响应更新注册表，返回有效 symbol 列表。"""
        all_symbols = self.parse_exchange_info(data)
        self._symbols.clear()
        valid: list[str] = []
        for info in all_symbols:
            if self.filter_symbol(info):
                self._symbols[info.symbol] = info
                valid.append(info.symbol)
        logger.info("symbol_registry_updated total=%d valid=%d", len(all_symbols), len(valid))
        return valid

    async def fetch_from_api(
        self,
        base_url: str = "https://fapi.binance.com",
        client: httpx.AsyncClient | None = None,
    ) -> list[str]:
        """从 Binance REST API 获取 exchangeInfo 并更新注册表。"""
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=10)
        try:
            resp = await client.get(f"{base_url}/fapi/v1/exchangeInfo")
            resp.raise_for_status()
            data = resp.json()
            return self.update_from_exchange_info(data)
        finally:
            if own_client:
                await client.aclose()

    def get_symbol(self, symbol: str) -> SymbolInfo | None:
        return self._symbols.get(symbol)

    def get_all_symbols(self) -> list[str]:
        return list(self._symbols.keys())
