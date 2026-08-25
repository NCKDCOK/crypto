"""Dashboard API 测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.dashboard import DashboardService
from src.domain import (
    AnalysisEvent,
    ConfidenceState,
    Direction,
    Evidence,
    EvidenceFamily,
    State,
    Veto,
    VetoSeverity,
    VetoType,
)


def _make_event(
    symbol="BTCUSDT",
    new_state=State.SUSPECTED_START,
    direction=Direction.LONG,
    confidence=ConfidenceState.CONFIDENT,
):
    return AnalysisEvent(
        symbol=symbol,
        direction=direction,
        previous_state=State.SLEEPING,
        new_state=new_state,
        evidence=[
            Evidence(
                family=EvidenceFamily.ANOMALY,
                type="volume_z",
                value=4.72,
                threshold=3.0,
                passed=True,
            ),
        ],
        vetoes=[
            Veto(type=VetoType.RAPID_RETRACE, triggered=False, severity=VetoSeverity.HARD),
        ],
        asof=1672515782136,
        confidence_state=confidence,
    )


class TestDashboardService:
    def test_market_radar(self):
        svc = DashboardService()
        svc.update_event(_make_event("BTCUSDT"))
        svc.update_event(_make_event("ETHUSDT", State.ANOMALY))
        radar = svc.get_market_radar()
        assert len(radar) == 2
        assert any(r["symbol"] == "BTCUSDT" for r in radar)

    def test_symbol_detail(self):
        svc = DashboardService()
        svc.update_event(_make_event("BTCUSDT"))
        detail = svc.get_symbol_detail("BTCUSDT")
        assert detail is not None
        assert detail["symbol"] == "BTCUSDT"
        assert len(detail["evidence"]) == 1
        assert len(detail["vetoes"]) == 1

    def test_symbol_not_found(self):
        svc = DashboardService()
        assert svc.get_symbol_detail("NONEXIST") is None

    def test_signal_history(self):
        svc = DashboardService()
        event = _make_event("BTCUSDT")
        svc.update_event(event)
        await_result = svc.repository.save_analysis_event(event)
        # save_analysis_event is async, but we can call it synchronously for test
        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(svc.repository.save_analysis_event(event))
        loop.close()

        history = svc.get_signal_history()
        assert len(history) == 1
        assert history[0]["symbol"] == "BTCUSDT"


class TestFastAPIApp:
    def test_health_check(self):
        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_radar_empty(self):
        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/radar")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_radar_with_data(self):
        svc = DashboardService()
        svc.update_event(_make_event("BTCUSDT"))
        app = create_app(svc)
        client = TestClient(app)
        resp = client.get("/api/radar")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "BTCUSDT"

    def test_symbol_detail_404(self):
        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/symbol/NONEXIST")
        assert resp.status_code == 404

    def test_symbol_detail_found(self):
        svc = DashboardService()
        svc.update_event(_make_event("BTCUSDT"))
        app = create_app(svc)
        client = TestClient(app)
        resp = client.get("/api/symbol/BTCUSDT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTCUSDT"
        assert "evidence" in data
        assert "vetoes" in data

    def test_signals(self):
        svc = DashboardService()
        app = create_app(svc)
        client = TestClient(app)
        resp = client.get("/api/signals")
        assert resp.status_code == 200
