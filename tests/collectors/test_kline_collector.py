"""Kline Collector 测试 — 解析、仅 closed bar 标记。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from src.clock import TestClock
from src.collectors.kline_collector import KlineCollector, parse_kline_payload
from src.domain import KlineEvent, KlineInterval

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


class TestParseKlinePayload:
    def test_parse_open_kline(self):
        """is_closed=false 的实时 bar。"""
        payload = {
            "e": "kline",
            "E": 1672515782136,
            "s": "BTCUSDT",
            "k": {
                "t": 1672515780000,
                "T": 1672515839999,
                "s": "BTCUSDT",
                "i": "1m",
                "f": 100,
                "L": 200,
                "o": "50000.00",
                "c": "50001.00",
                "h": "50002.00",
                "l": "49999.00",
                "v": "10.5",
                "n": 150,
                "x": False,
                "q": "525000.00",
            },
        }
        event = parse_kline_payload(payload, receive_time=1672515782137)
        assert event is not None
        assert event.symbol == "BTCUSDT"
        assert event.interval == KlineInterval.M1
        assert event.is_closed is False
        assert event.open == Decimal("50000.00")
        assert event.close == Decimal("50001.00")
        assert event.volume == Decimal("10.5")
        assert event.trade_count == 150

    def test_parse_closed_kline(self):
        """is_closed=true 的 bar 才能进入慢周期确认。"""
        payload = {
            "e": "kline",
            "E": 1672515840000,
            "s": "BTCUSDT",
            "k": {
                "t": 1672515780000,
                "T": 1672515839999,
                "s": "BTCUSDT",
                "i": "1m",
                "o": "50000.00",
                "c": "50005.00",
                "h": "50010.00",
                "l": "49999.00",
                "v": "15.0",
                "n": 300,
                "x": True,
                "q": "750000.00",
            },
        }
        event = parse_kline_payload(payload, receive_time=1672515840001)
        assert event is not None
        assert event.is_closed is True

    def test_parse_non_kline_returns_none(self):
        payload = {"e": "aggTrade"}
        assert parse_kline_payload(payload, receive_time=123) is None

    def test_parse_from_fixture(self):
        """从 fixture 文件解析 — 第一条未闭合，第二条闭合。"""
        with open(FIXTURES / "kline_stream.jsonl") as f:
            lines = [json.loads(line.strip()) for line in f if line.strip()]

        e1 = parse_kline_payload(lines[0], receive_time=1672515782137)
        e2 = parse_kline_payload(lines[1], receive_time=1672515840001)

        assert e1 is not None
        assert e1.is_closed is False  # 未闭合

        assert e2 is not None
        assert e2.is_closed is True  # 闭合
        assert e2.close == Decimal("50005.00")

    def test_only_closed_bar_for_slow_confirmed(self):
        """验证仅 closed bar 进入慢周期确认。"""
        open_payload = {
            "e": "kline", "E": 100, "s": "BTCUSDT",
            "k": {"t": 0, "T": 999, "s": "BTCUSDT", "i": "1m",
                  "o": "1", "c": "2", "h": "3", "l": "0", "v": "1", "n": 1, "x": False},
        }
        closed_payload = dict(open_payload)
        closed_payload["k"] = dict(open_payload["k"], x=True)

        e_open = parse_kline_payload(open_payload, receive_time=101)
        e_closed = parse_kline_payload(closed_payload, receive_time=102)

        assert not e_open.is_closed  # 不能进入慢周期确认
        assert e_closed.is_closed    # 可以进入慢周期确认
