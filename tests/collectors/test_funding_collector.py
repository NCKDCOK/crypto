"""Funding/Premium Collector 测试。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.clock import TestClock
from src.collectors.funding_collector import (
    FundingPremiumCollector,
    parse_premium_index_response,
)
from src.health.rate_limiter import RateLimiter

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _load_premium_response() -> dict:
    with open(FIXTURES / "premiumIndex_response.json") as f:
        return json.load(f)


class TestParsePremiumIndexResponse:
    def test_parse_normal(self):
        data = _load_premium_response()
        snap = parse_premium_index_response(data, "BTCUSDT", receive_time=1672515782999)
        assert snap is not None
        assert snap.symbol == "BTCUSDT"
        assert snap.mark_price == Decimal("50000.10")
        assert snap.index_price == Decimal("49990.00")
        assert snap.last_funding_rate == Decimal("0.00010000")
        assert snap.next_funding_time == 1672516800000

    def test_premium_calculated(self):
        """premium = mark_price - index_price（本地计算）。"""
        data = _load_premium_response()
        snap = parse_premium_index_response(data, "BTCUSDT", receive_time=1672515782999)
        assert snap is not None
        assert snap.premium == Decimal("50000.10") - Decimal("49990.00")
        assert snap.premium == Decimal("10.10")

    def test_parse_empty_returns_none(self):
        assert parse_premium_index_response({}, "BTCUSDT", receive_time=123) is None

    def test_event_time_from_response_time(self):
        data = {
            "symbol": "BTCUSDT",
            "markPrice": "50000",
            "indexPrice": "49990",
            "lastFundingRate": "0.0001",
            "nextFundingTime": 1672516800000,
            "time": 888,
        }
        snap = parse_premium_index_response(data, "BTCUSDT", receive_time=1000)
        assert snap is not None
        assert snap.event_time == 888


class TestFundingPollerPollOne:
    async def test_poll_one_success(self):
        rate_limiter = RateLimiter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.headers = httpx.Headers({})
        mock_resp.json.return_value = _load_premium_response()
        mock_client.request = AsyncMock(return_value=mock_resp)
        rate_limiter._client = mock_client
        rate_limiter._owns_client = False

        clock = TestClock(initial_ms=1672515782000)
        received = []

        poller = FundingPremiumCollector(
            symbols=["BTCUSDT"],
            rate_limiter=rate_limiter,
            clock=clock,
            on_snapshot=lambda s: received.append(s),
        )

        snap = await poller.poll_one("BTCUSDT")
        assert snap is not None
        assert snap.symbol == "BTCUSDT"
        assert snap.premium == Decimal("10.10")
        assert len(received) == 1

    async def test_poll_one_error_returns_none(self):
        rate_limiter = RateLimiter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.headers = httpx.Headers({})
        mock_resp.json.return_value = {}
        mock_client.request = AsyncMock(return_value=mock_resp)
        rate_limiter._client = mock_client
        rate_limiter._owns_client = False

        poller = FundingPremiumCollector(
            symbols=["BTCUSDT"],
            rate_limiter=rate_limiter,
        )

        snap = await poller.poll_one("BTCUSDT")
        assert snap is None
