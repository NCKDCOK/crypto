"""Symbol Registry 测试。"""

from __future__ import annotations

import json
from pathlib import Path

from src.collectors.symbol_registry import SymbolRegistry

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


class TestSymbolRegistry:
    def _load_fixture(self) -> dict:
        with open(FIXTURES / "exchangeInfo_sample.json") as f:
            return json.load(f)

    def test_parse_exchange_info(self):
        reg = SymbolRegistry()
        data = self._load_fixture()
        all_symbols = reg.parse_exchange_info(data)
        assert len(all_symbols) == 5

    def test_filter_perpetual_trading_usdt(self):
        reg = SymbolRegistry(quote_asset="USDT")
        data = self._load_fixture()
        valid = reg.update_from_exchange_info(data)
        # BTCUSDT, ETHUSDT 通过；BTCUSDT_240329(交割) / DELISTEDUSDT(下架) / BTCBUSD(非USDT) 不通过
        assert "BTCUSDT" in valid
        assert "ETHUSDT" in valid
        assert len(valid) == 2

    def test_delisted_filtered(self):
        reg = SymbolRegistry(quote_asset="USDT")
        data = self._load_fixture()
        reg.update_from_exchange_info(data)
        assert reg.get_symbol("DELISTEDUSDT") is None

    def test_non_perpetual_filtered(self):
        reg = SymbolRegistry(quote_asset="USDT")
        data = self._load_fixture()
        reg.update_from_exchange_info(data)
        # 交割合约不纳入
        assert reg.get_symbol("BTCUSDT_240329") is None

    def test_wrong_quote_asset_filtered(self):
        reg = SymbolRegistry(quote_asset="USDT")
        data = self._load_fixture()
        reg.update_from_exchange_info(data)
        assert reg.get_symbol("BTCBUSD") is None

    def test_exclude_patterns(self):
        reg = SymbolRegistry(quote_asset="USDT", exclude_patterns=["BTC"])
        data = self._load_fixture()
        valid = reg.update_from_exchange_info(data)
        assert "BTCUSDT" not in valid
        assert "ETHUSDT" in valid

    def test_get_all_symbols(self):
        reg = SymbolRegistry(quote_asset="USDT")
        data = self._load_fixture()
        reg.update_from_exchange_info(data)
        all_syms = reg.get_all_symbols()
        assert set(all_syms) == {"BTCUSDT", "ETHUSDT"}

    def test_get_symbol_info(self):
        reg = SymbolRegistry(quote_asset="USDT")
        data = self._load_fixture()
        reg.update_from_exchange_info(data)
        info = reg.get_symbol("BTCUSDT")
        assert info is not None
        assert info.contract_type == "PERPETUAL"
        assert info.base_asset == "BTC"
