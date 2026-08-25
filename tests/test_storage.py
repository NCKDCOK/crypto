"""Storage InMemoryRepository 测试。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.domain import (
    AnalysisEvent,
    ConfidenceState,
    OpenInterestSnapshot,
    State,
)
from src.storage import InMemoryRepository


class TestInMemoryRepository:
    @pytest.fixture
    def repo(self):
        return InMemoryRepository()

    async def test_save_and_get_oi_asof(self, repo):
        snap = OpenInterestSnapshot(
            symbol="BTCUSDT",
            event_time=1000000,
            receive_time=1000000,
            open_interest=Decimal("100.0"),
            freshness_ms=0,
        )
        await repo.save_event(snap)

        # Exact match
        result = await repo.get_oi_snapshot_asof("BTCUSDT", 1000000, 1000)
        assert result is not None
        assert result.open_interest == Decimal("100.0")

        # Within tolerance
        result = await repo.get_oi_snapshot_asof("BTCUSDT", 1000500, 1000)
        assert result is not None

        # Outside tolerance
        result = await repo.get_oi_snapshot_asof("BTCUSDT", 1002000, 1000)
        assert result is None

    async def test_list_transitions(self, repo):
        ev = AnalysisEvent(
            symbol="BTCUSDT",
            previous_state=State.SLEEPING,
            new_state=State.ANOMALY,
            asof=1000000,
            confidence_state=ConfidenceState.CONFIDENT,
        )
        await repo.save_analysis_event(ev)
        results = await repo.list_transitions("BTCUSDT", 0, 2000000)
        assert len(results) == 1
        assert results[0].new_state == State.ANOMALY

    async def test_list_transitions_empty(self, repo):
        results = await repo.list_transitions("BTCUSDT", 0, 2000000)
        assert results == []
