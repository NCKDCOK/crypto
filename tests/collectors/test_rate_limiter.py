"""RateLimiter 测试 — 模拟 429 + Retry-After，不产生 retry storm；熔断触发。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.health.rate_limiter import (
    CircuitOpenError,
    IPBannedError,
    RateLimiter,
    RateLimiterConfig,
)


def _mock_response(status: int, headers: dict | None = None) -> httpx.Response:
    """构造 mock httpx.Response。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.headers = httpx.Headers(headers or {})
    resp.json.return_value = {"ok": True}
    return resp


class TestRateLimiterNormal:
    async def test_successful_request(self):
        config = RateLimiterConfig()
        limiter = RateLimiter(config=config)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_mock_response(200))
        limiter._client = mock_client
        limiter._owns_client = False

        resp = await limiter.request("GET", "https://example.com/api")
        assert resp.status_code == 200
        assert limiter.state.total_requests == 1
        assert limiter.state.consecutive_429 == 0


class TestRateLimiter429:
    async def test_429_with_retry_after_then_success(self):
        """429 + Retry-After → 退避后重试成功。"""
        config = RateLimiterConfig(
            weight_limit_per_minute=100,
            initial_backoff_ms=10,  # 短退避加速测试
            circuit_breaker_threshold=5,  # 高阈值避免熔断
        )
        limiter = RateLimiter(config=config)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        # 第一次 429，第二次 200
        mock_client.request = AsyncMock(
            side_effect=[
                _mock_response(429, {"Retry-After": "0.01"}),
                _mock_response(200, {"X-MBX-USED-WEIGHT-1M": "50"}),
            ]
        )
        limiter._client = mock_client
        limiter._owns_client = False

        resp = await limiter.request("GET", "https://example.com/api")
        assert resp.status_code == 200
        assert limiter.state.total_429 == 1
        assert limiter.state.weight_used == 50
        # 重置了 consecutive_429
        assert limiter.state.consecutive_429 == 0

    async def test_circuit_opens_after_threshold(self):
        """连续 429 达到阈值 → 熔断打开。"""
        config = RateLimiterConfig(
            circuit_breaker_threshold=3,
            initial_backoff_ms=10,
        )
        limiter = RateLimiter(config=config)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(
            return_value=_mock_response(429, {"Retry-After": "0.01"})
        )
        limiter._client = mock_client
        limiter._owns_client = False

        # 3 次 429 后熔断打开，第 4 次请求直接拒绝
        with pytest.raises(CircuitOpenError):
            await limiter.request("GET", "https://example.com/api")
        assert limiter.state.circuit_open is True
        assert limiter.state.total_429 == 3

    async def test_no_retry_storm(self):
        """429 重试不超过 max_retries 次。"""
        config = RateLimiterConfig(
            initial_backoff_ms=1,
            circuit_breaker_threshold=100,  # 不熔断
            max_backoff_ms=10,
        )
        limiter = RateLimiter(config=config)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(
            return_value=_mock_response(429, {"Retry-After": "0.001"})
        )
        limiter._client = mock_client
        limiter._owns_client = False

        resp = await limiter.request("GET", "https://example.com/api")
        # 最终返回 429 响应（重试耗尽）
        assert resp.status_code == 429
        # 最多请求 5 次（max_retries）
        assert mock_client.request.call_count == 5

    async def test_circuit_open_rejects_request(self):
        """熔断打开时新请求直接拒绝。"""
        config = RateLimiterConfig(circuit_breaker_threshold=1)
        limiter = RateLimiter(config=config)

        # 手动设置熔断状态
        import time

        limiter.state.circuit_open = True
        limiter.state.circuit_open_until = time.monotonic() + 100

        with pytest.raises(CircuitOpenError):
            await limiter.request("GET", "https://example.com/api")


class TestRateLimiter418:
    async def test_418_raises_ip_banned(self):
        """418 触发 IPBannedError 并熔断。"""
        config = RateLimiterConfig()
        limiter = RateLimiter(config=config)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(
            return_value=_mock_response(418, {"Retry-After": "120"})
        )
        limiter._client = mock_client
        limiter._owns_client = False

        with pytest.raises(IPBannedError):
            await limiter.request("GET", "https://example.com/api")
        assert limiter.state.total_418 == 1
        assert limiter.state.circuit_open is True


class TestRateLimiterWeightTracking:
    async def test_weight_updated_from_header(self):
        """成功响应的 X-MBX-USED-WEIGHT-1M 头被读取。"""
        limiter = RateLimiter()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(
            return_value=_mock_response(200, {"X-MBX-USED-WEIGHT-1M": "750"})
        )
        limiter._client = mock_client
        limiter._owns_client = False

        await limiter.request("GET", "https://example.com/api")
        assert limiter.state.weight_used == 750
