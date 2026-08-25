"""Replay Calibration 测试 — V1.2 §42。"""

from __future__ import annotations

from pathlib import Path

from src.replay.calibration import CalibrationStore, SetupSnapshot


class TestCalibrationStore:
    def test_add_and_get(self):
        store = CalibrationStore()
        s = SetupSnapshot(symbol="BTCUSDT", asof=1000, setup_type="BREAKOUT_START",
                          state="START_CONFIRMED", opportunity=85.0,
                          subscores={"startup_quality": {"score": 82}})
        store.add(s)
        assert len(store.get_all()) == 1
        assert len(store.get_by_setup("BREAKOUT_START")) == 1

    def test_backfill_future(self):
        store = CalibrationStore()
        store.add(SetupSnapshot(symbol="BTCUSDT", asof=1000, setup_type="BREAKOUT_START",
                                state="START_CONFIRMED", opportunity=85.0))
        n = store.backfill_future("BTCUSDT", 1000, 0.02, 0.03, 0.05, 0.06, 0.01)
        assert n == 1
        s = store.get_all()[0]
        assert s.future_15m == 0.03
        assert s.mfe == 0.06

    def test_stats_by_bucket(self):
        store = CalibrationStore()
        for i, (opp, fut) in enumerate([(85, 0.02), (90, 0.03), (88, -0.01), (70, 0.05)]):
            store.add(SetupSnapshot(symbol="X", asof=i, setup_type="S", state="C", opportunity=opp))
            store.backfill_future("X", i, fut, fut, fut, fut, 0)
        stats = store.stats_by_bucket("opportunity", 80, "future_15m")
        # >80: 3 samples, positive 2/3
        assert stats["sample"] == 3
        assert stats["positive_rate"] is not None

    def test_insufficient_sample(self):
        store = CalibrationStore()
        stats = store.stats_by_bucket("opportunity", 80)
        assert stats["sample"] == 0
        assert stats["positive_rate"] is None

    def test_persistence(self, tmp_path):
        store = CalibrationStore()
        store.add(SetupSnapshot(symbol="X", asof=1, setup_type="S", state="C", opportunity=85,
                                entry_zone=(100, 105), invalidation=95, tp1=110))
        p = tmp_path / "cal.json"
        store.save_to_json(p)
        store2 = CalibrationStore()
        store2.load_from_json(p)
        assert len(store2.get_all()) == 1
        s = store2.get_all()[0]
        assert s.opportunity == 85
        assert s.entry_zone == (100, 105)
