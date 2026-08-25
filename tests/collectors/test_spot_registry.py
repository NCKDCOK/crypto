"""Spot Symbol Registry 测试 — V1.2 §9。"""

from __future__ import annotations

from src.collectors.spot_registry import SpotSymbolRegistry


class TestSpotRegistry:
    def test_update_and_has_spot(self):
        reg = SpotSymbolRegistry(quote_asset="USDT")
        data = {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT"},
            {"symbol": "ETHUSDT", "status": "TRADING", "baseAsset": "ETH", "quoteAsset": "USDT"},
            {"symbol": "XXXUSDT", "status": "BREAK", "baseAsset": "XXX", "quoteAsset": "USDT"},
            {"symbol": "BTCBUSD", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "BUSD"},
        ]}
        reg.update_from_exchange_info(data)
        assert reg.has_spot("BTCUSDT") is True
        assert reg.has_spot("ETHUSDT") is True
        assert reg.has_spot("XXXUSDT") is False  # 非 TRADING
        assert reg.has_spot("1000SATSUSDT") is False  # 无现货

    def test_perp_to_spot_identity(self):
        reg = SpotSymbolRegistry()
        reg._symbols = {"BTCUSDT"}
        assert reg.perp_to_spot("BTCUSDT") == "BTCUSDT"
        assert reg.perp_to_spot("FOOUSDT") is None

    def test_no_spot_marked_unavailable(self):
        """无现货的 symbol → perp_to_spot None（不伪造）。"""
        reg = SpotSymbolRegistry()
        reg._symbols = set()
        assert reg.perp_to_spot("ANYUSDT") is None
        assert reg.has_spot("ANYUSDT") is False
