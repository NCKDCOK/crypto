"""SQLite Repository 测试 — V1.2 持久化基础层。"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from src.domain import (
    AnalysisEvent,
    ConfidenceState,
    Direction,
    Evidence,
    EvidenceFamily,
    FundingRateSnapshot,
    KlineEvent,
    KlineInterval,
    OpenInterestSnapshot,
    State,
    Veto,
    VetoSeverity,
    VetoType,
)
from src.storage.sqlite_repository import SqliteRepository


def _kline(symbol="BTCUSDT", interval="1m", open_time=1000, closed=True) -> KlineEvent:
    return KlineEvent(
        symbol=symbol, interval=KlineInterval(interval), open_time=open_time,
        close_time=open_time + 60_000, event_time=open_time, receive_time=open_time,
        open=Decimal("100"), high=Decimal("110"), low=Decimal("99"), close=Decimal("105"),
        volume=Decimal("10"), quote_volume=Decimal("1050"), trade_count=100,
        is_closed=closed,
    )


def _oi(symbol="BTCUSDT", event_time=1000) -> OpenInterestSnapshot:
    return OpenInterestSnapshot(
        symbol=symbol, event_time=event_time, receive_time=event_time,
        open_interest=Decimal("50000"), source="binance_rest_openinterest", freshness_ms=0,
    )


def _funding(symbol="BTCUSDT", event_time=1000) -> FundingRateSnapshot:
    return FundingRateSnapshot(
        symbol=symbol, event_time=event_time, receive_time=event_time,
        mark_price=Decimal("100"), index_price=Decimal("99.9"),
        last_funding_rate=Decimal("0.0001"), next_funding_time=event_time + 1000,
        premium=Decimal("0.1"), source="binance_rest_premiumindex",
    )


def _analysis_event(symbol="BTCUSDT", asof=1000) -> AnalysisEvent:
    return AnalysisEvent(
        symbol=symbol, direction=Direction.LONG,
        previous_state=State.ANOMALY, new_state=State.START_CONFIRMED,
        evidence=[Evidence(family=EvidenceFamily.FLOW, type="taker_delta", value=10000.0, passed=True)],
        vetoes=[Veto(type=VetoType.RAPID_RETRACE, triggered=True, severity=VetoSeverity.HARD)],
        asof=asof, confidence_state=ConfidenceState.CONFIDENT,
    )


class TestKlinePersistence:
    def test_save_and_load_closed_kline(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_kline(_kline(open_time=1000))
        repo.save_kline(_kline(open_time=1060))
        bars = repo.get_recent_klines("BTCUSDT", "1m", limit=10)
        assert len(bars) == 2
        assert bars[0].open_time == 1000
        assert bars[1].open_time == 1060
        repo.close()

    def test_unclosed_kline_not_saved(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_kline(_kline(open_time=1000, closed=False))
        assert repo.get_recent_klines("BTCUSDT", "1m") == []
        repo.close()

    def test_upsert_same_open_time(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_kline(_kline(open_time=1000))
        repo.save_kline(_kline(open_time=1000))  # 同 open_time → replace
        assert len(repo.get_recent_klines("BTCUSDT", "1m")) == 1
        repo.close()


class TestOIandFunding:
    def test_oi_save_and_last_write(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_oi_snapshot(_oi(event_time=1000))
        repo.save_oi_snapshot(_oi(event_time=2000))
        assert repo.get_last_write_ms() == 2000
        recent = repo.get_recent_oi("BTCUSDT")
        assert len(recent) == 2
        repo.close()

    def test_funding_save(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_funding_snapshot(_funding(event_time=1000))
        assert repo.get_last_write_ms() == 1000
        repo.close()


class TestAnalysisEvent:
    def test_save_and_list_transitions(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_analysis_event(_analysis_event(asof=1000))
        repo.save_analysis_event(_analysis_event(asof=2000))
        evs = repo.list_transitions("BTCUSDT", 0, 3000)
        assert len(evs) == 2
        assert evs[0].new_state == State.START_CONFIRMED
        assert len(evs[0].evidence) == 1
        assert len(evs[0].vetoes) == 1
        assert evs[0].vetoes[0].type == VetoType.RAPID_RETRACE
        repo.close()


class TestTradePlan:
    def test_save_active_and_expire(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_trade_plan("BTCUSDT", 1000, {"entry": 100})
        repo.save_trade_plan("BTCUSDT", 2000, {"entry": 110})
        plan = repo.get_active_trade_plan("BTCUSDT")
        assert plan is not None and plan["entry"] == 110
        # 过期 3000 之前创建的所有 plan（模拟长时间停机）
        n = repo.expire_trade_plans("BTCUSDT", 3000)
        assert n == 2
        assert repo.get_active_trade_plan("BTCUSDT") is None
        repo.close()


class TestLastWrite:
    def test_empty_db_no_last_write(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        assert repo.get_last_write_ms() is None
        repo.close()

    def test_last_write_max_across_tables(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_kline(_kline(open_time=1000))  # receive_time 1000
        repo.save_oi_snapshot(_oi(event_time=5000))  # receive_time 5000
        assert repo.get_last_write_ms() == 5000
        repo.close()


class TestLatestKlineOpenTime:
    def test_latest_open_time(self, tmp_path):
        repo = SqliteRepository(tmp_path / "t.db")
        repo.save_kline(_kline(open_time=1000))
        repo.save_kline(_kline(open_time=2000))
        assert repo.get_latest_kline_open_time("BTCUSDT", "1m") == 2000
        assert repo.get_latest_kline_open_time("ETHUSDT", "1m") is None
        repo.close()
