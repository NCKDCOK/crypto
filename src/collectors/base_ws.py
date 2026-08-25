"""WebSocket 基础 collector — 连接管理、重连、ping/pong、组合流解析。

依据：Binance WS Connect 文档
- Base: wss://fstream.binance.com
- 三分流: /public /market /private
- 组合流封装: {"stream":.., "data":..}
- symbol 全小写
- 每 3 分钟服务端发 ping，客户端 10 分钟内须回 pong
- 单连接最多订阅 1024 streams
- 连接有效期 ≤ 24h

只采集与标准化，不分析。
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import websockets
from websockets.asyncio.client import connect

from src.clock import Clock, SystemClock

logger = logging.getLogger(__name__)


@dataclass
class WSStreamConfig:
    """单个 WS 连接的配置。"""

    base_url: str = "wss://fstream.binance.com"
    route: str = "/market"  # /public /market /private
    streams: list[str] = field(default_factory=list)
    reconnect_delay_ms: int = 1000
    max_reconnect_delay_ms: int = 30_000
    ping_interval_s: float = 180.0  # 3 分钟

    def build_url(self) -> str:
        """构建组合流 URL。"""
        if not self.streams:
            raise ValueError("no streams configured")
        streams_str = "/".join(self.streams)
        return f"{self.base_url}{self.route}/stream?streams={streams_str}"


@dataclass
class WSStats:
    """WS 连接运行时统计。"""

    connected: bool = False
    subscribed: bool = False
    message_count: int = 0
    reconnect_count: int = 0
    last_event_time: int | None = None
    last_receive_time: int | None = None


class BaseWSCollector(ABC):
    """WS collector 基类 — 管理连接、重连、消息分发。

    子类实现 parse_payload() 将原始 payload 转为标准化事件，
    实现 on_event() 处理事件。
    """

    def __init__(
        self,
        config: WSStreamConfig,
        clock: Clock | None = None,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.stats = WSStats()
        self._running = False
        self._ws: Any = None
        self._reconnect_delay = float(config.reconnect_delay_ms) / 1000

    @abstractmethod
    def parse_payload(self, stream: str, payload: dict) -> Any:
        """将 Binance WS payload 解析为标准化事件。返回 None 表示跳过。"""
        ...

    @abstractmethod
    async def on_event(self, event: Any) -> None:
        """处理标准化事件。"""
        ...

    def _build_url(self) -> str:
        return self.config.build_url()

    def _extract_payload(self, raw: str) -> tuple[str | None, dict | None]:
        """从组合流消息中提取 stream 名和 payload。

        组合流格式: {"stream": "<name>", "data": <payload>}
        原始流格式: <payload>（无 stream 字段）
        """
        msg = json.loads(raw)
        if isinstance(msg, dict) and "stream" in msg and "data" in msg:
            return msg["stream"], msg["data"]
        return None, msg if isinstance(msg, dict) else None

    async def _connect_and_listen(self) -> None:
        """单次连接的生命周期：连接 → 接收 → 断开。"""
        url = self._build_url()
        logger.info("ws_connecting url=%s", url)
        try:
            async with connect(
                url,
                ping_interval=self.config.ping_interval_s,
                ping_timeout=600,
            ) as ws:
                self._ws = ws
                self.stats.connected = True
                self.stats.subscribed = True
                logger.info("ws_connected url=%s", url)

                async for raw in ws:
                    self.stats.message_count += 1
                    self.stats.last_receive_time = self.clock.now_ms()

                    stream, payload = self._extract_payload(raw)
                    if payload is None:
                        continue

                    event = self.parse_payload(stream or "", payload)
                    if event is not None:
                        self.stats.last_event_time = self.clock.now_ms()
                        try:
                            await self.on_event(event)
                        except Exception:
                            logger.exception("ws_on_event_error stream=%s", stream)
        except Exception as e:
            logger.warning("ws_disconnected error=%s", e)
        finally:
            self.stats.connected = False
            self.stats.subscribed = False
            self._ws = None

    async def _reconnect_loop(self) -> None:
        """重连循环：连接断开后指数退避重连。"""
        while self._running:
            await self._connect_and_listen()
            if not self._running:
                break
            self.stats.reconnect_count += 1
            delay = min(
                self._reconnect_delay * (2 ** (self.stats.reconnect_count - 1)),
                float(self.config.max_reconnect_delay_ms) / 1000,
            )
            logger.info(
                "ws_reconnect attempt=%d delay=%.1fs",
                self.stats.reconnect_count,
                delay,
            )
            await asyncio.sleep(delay)

    async def start(self) -> None:
        """启动 collector（后台运行）。"""
        self._running = True
        asyncio.create_task(self._reconnect_loop())

    async def stop(self) -> None:
        """停止 collector。"""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        logger.info("ws_stopped")

    async def run_until_stopped(self) -> None:
        """同步运行直到 stop() 被调用。用于测试。"""
        self._running = True
        await self._reconnect_loop()
