"""OI Poller 测试 — 解析、单位、per-symbol 隔离。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.clock import TestClock
from src.collectors.oi_poller import OIPoller, parse_open_interest_response
from src.health.rate_limiter import RateLimiter

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _load_oi_response() -> dict:
    with open(FIXTURES / "openInterest_response.json") as f:
        return json.load(f)


class TestParseOpenInterestResponse:
    def test_parse_normal(self):
        data = _load_oi_response()
        snap = parse_open_interest_response(data, "BTCUSDT", receive_time=1672515782999)
        assert snap is not None
        assert snap.symbol == "BTCUSDT"
        assert snap.open_interest == Decimal("50000.5")
        assert snap.source == "binance_rest_openinterest"
        assert snap.freshness_ms == 0

    def test_oi_unit_is_base_asset(self):
        """OI 单位 = 基础资产数量，不是美元名义。

        openInterest=50000.5 是 BTC 数量，不是美元。
        """
        data = _load_oi_response()
        snap = parse_open_interest_response(data, "BTCUSDT", receive_time=1672515782999)
        assert snap is not None
        # 50000.5 BTC，不是 50000.5 USDT
        assert snap.open_interest == Decimal("50000.5")

    def test_parse_empty_returns_none(self):
        assert parse_open_interest_response({}, "BTCUSDT", receive_time=123) is None

    def test_event_time_from_response_time(self):
        """event_time 取 Binance time 字段。"""
        data = {"openInterest": "100", "symbol": "BTCUSDT", "time": 999}
        snap = parse_open_interest_response(data, "BTCUSDT", receive_time=1000)
        assert snap is not None
        assert snap.event_time == 999


class TestOIPollerPollOne:
    async def test_poll_one_success(self):
        """单 symbol 轮询成功。"""
        rate_limiter = RateLimiter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.headers = httpx.Headers({})
        mock_resp.json.return_value = _load_oi_response()
        mock_client.request = AsyncMock(return_value=mock_resp)
        rate_limiter._client = mock_client
        rate_limiter._owns_client = False

        clock = TestClock(initial_ms=1672515782000)
        received = []

        poller = OIPoller(
            symbols=["BTCUSDT"],
            rate_limiter=rate_limiter,
            clock=clock,
            on_snapshot=lambda s: received.append(s),
        )

        snap = await poller.poll_one("BTCUSDT")
        assert snap is not None
        assert snap.symbol == "BTCUSDT"
        assert len(received) == 1

    async def test_poll_one_error_doesnt_block_others(self):
        """单 symbol 失败返回 None，不影响其他 symbol。"""
        rate_limiter = RateLimiter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp_err = MagicMock(spec=httpx.Response)
        mock_resp_err.status_code = 500
        mock_resp_err.headers = httpx.Headers({})
        mock_resp_err.json.return_value = {}
        mock_resp_ok = MagicMock(spec=httpx.Response)
        mock_resp_ok.status_code = 200
        mock_resp_ok.headers = httpx.Headers({})
        mock_resp_ok.json.return_value = _load_oi_response()
        mock_client.request = AsyncMock(
            side_effect=[mock_resp_err, mock_resp_ok]
        )
        rate_limiter._client = mock_client
        rate_limiter._owns_client = False

        clock = TestClock(initial_ms=1672515782000)
        poller = OIPoller(
            symbols=["BADUSDT", "BTCUSDT"],
            rate_limiter=rate_limiter,
            clock=clock,
        )

        # 第一个 symbol 失败
        snap1 = await poller.poll_one("BADUSDT")
        assert snap1 is None

        # 第二个 symbol 正常
        snap2 = await poller.poll_one("BTCUSDT")
        assert snap2 is not None
        assert snap2.symbol == "BTCUSDT"
