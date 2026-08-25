"""集中 REST RateLimiter — 权重预算、Retry-After、指数退避、全局熔断。

依据：epic-01 Task 1.6, docs/DATA_HEALTH.md §8

- 读取 Binance 响应头 X-MBX-USED-WEIGHT-*，维护全局权重预算。
- 收到 429：读取 Retry-After，全局限流退避。
- 指数退避 + 全局熔断，避免触发 418（IP 封禁）。
- 所有 REST 请求必须通过本 RateLimiter，禁止各模块各自轮询。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class RateLimiterConfig:
    """RateLimiter 配置。"""

    weight_limit_per_minute: int = 1200
    initial_backoff_ms: int = 1000
    max_backoff_ms: int = 60_000
    circuit_breaker_threshold: int = 3


@dataclass
class RateLimiterState:
    """RateLimiter 运行时状态。"""

    weight_used: int = 0
    weight_limit: int = 1200
    consecutive_429: int = 0
    circuit_open: bool = False
    circuit_open_until: float = 0.0  # epoch seconds
    total_429: int = 0
    total_418: int = 0
    total_requests: int = 0


class RateLimiter:
    """集中 REST 限频器。

    所有 REST 请求通过 request() 方法发出，RateLimiter 负责：
    1. 熔断检查 — 熔断期间拒绝所有请求
    2. 发送请求
    3. 读取权重头更新预算
    4. 429/418 处理 — 退避 + 熔断
    """

    def __init__(
        self,
        config: RateLimiterConfig | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or RateLimiterConfig()
        self.state = RateLimiterState(weight_limit=self.config.weight_limit_per_minute)
        self._client = client
        self._owns_client = client is None
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _check_circuit(self) -> bool:
        """检查熔断状态。返回 True 如果熔断打开（拒绝请求）。"""
        if self.state.circuit_open:
            if time.monotonic() < self.state.circuit_open_until:
                return True
            # 熔断时间到，半开
            self.state.circuit_open = False
            self.state.consecutive_429 = 0
            logger.info("rate_limiter_circuit_half_open")
        return False

    def _update_weight_from_headers(self, headers: httpx.Headers) -> None:
        """从响应头读取权重使用量。"""
        # Binance 使用 X-MBX-USED-WEIGHT-1M 头
        for key in ("X-MBX-USED-WEIGHT-1M", "x-mbx-used-weight-1m"):
            if key in headers:
                try:
                    self.state.weight_used = int(headers[key])
                except ValueError:
                    pass
                break

    def _handle_error(self, status_code: int, headers: httpx.Headers) -> float:
        """处理 429/418 错误。返回需要等待的秒数。"""
        if status_code == 429:
            self.state.total_429 += 1
            self.state.consecutive_429 += 1
            # 读取 Retry-After
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = self.config.initial_backoff_ms / 1000
            else:
                # 指数退避
                backoff = min(
                    self.config.initial_backoff_ms * (2 ** (self.state.consecutive_429 - 1)),
                    self.config.max_backoff_ms,
                )
                wait = backoff / 1000

            # 检查熔断
            if self.state.consecutive_429 >= self.config.circuit_breaker_threshold:
                self.state.circuit_open = True
                self.state.circuit_open_until = time.monotonic() + wait
                logger.warning(
                    "rate_limiter_circuit_open consecutive_429=%d wait=%.1fs",
                    self.state.consecutive_429,
                    wait,
                )
            else:
                logger.warning(
                    "rate_limiter_429 consecutive=%d wait=%.1fs",
                    self.state.consecutive_429,
                    wait,
                )
            return wait

        if status_code == 418:
            self.state.total_418 += 1
            # 418 = IP 被临时封禁，必须长时间等待
            retry_after = headers.get("Retry-After")
            wait = float(retry_after) if retry_after else 120.0
            self.state.circuit_open = True
            self.state.circuit_open_until = time.monotonic() + wait
            logger.error("rate_limiter_418_ip_banned wait=%.1fs", wait)
            return wait

        # 非 429/418 错误，重置连续计数
        self.state.consecutive_429 = 0
        return 0.0

    async def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """发送受 RateLimiter 保护的请求。

        - 熔断打开时抛出 CircuitOpenError。
        - 429 退避后重试（有限次）。
        - 418 熔断后抛出 IPBannedError。
        """
        async with self._lock:
            if self._check_circuit():
                raise CircuitOpenError(
                    f"circuit open until {self.state.circuit_open_until:.0f}"
                )

            self.state.total_requests += 1
            client = await self._get_client()

            max_retries = 5
            for attempt in range(max_retries):
                resp = await client.request(method, url, **kwargs)
                self._update_weight_from_headers(resp.headers)

                if resp.status_code in (429, 418):
                    wait = self._handle_error(resp.status_code, resp.headers)
                    if resp.status_code == 418:
                        raise IPBannedError(f"IP banned, wait {wait}s")
                    if self.state.circuit_open:
                        raise CircuitOpenError(
                            f"circuit opened after {self.state.consecutive_429} 429s"
                        )
                    if attempt < max_retries - 1:
                        logger.info("rate_limiter_retry attempt=%d wait=%.1fs", attempt + 1, wait)
                        await asyncio.sleep(wait)
                        continue
                    return resp

                # 成功 — 重置连续 429 计数
                self.state.consecutive_429 = 0
                return resp

            return resp  # 最后一次的响应


class CircuitOpenError(Exception):
    """熔断打开，请求被拒绝。"""


class IPBannedError(Exception):
    """IP 被 Binance 临时封禁（418）。"""
