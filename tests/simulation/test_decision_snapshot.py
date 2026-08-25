"""§11 DecisionSnapshotService — 周期内冻结，首页不秒级抖动。"""

from __future__ import annotations

from src.simulation import DecisionSnapshotService


class TestFrozenCycle:
    def test_first_update_frozen(self):
        svc = DecisionSnapshotService(interval_s=30.0)
        snap = svc.update("BTCUSDT", now_ms=1000, decision={"opportunity_score": 80.0})
        assert snap["frozen_at"] == 1000
        assert snap["decision"]["opportunity_score"] == 80.0

    def test_within_interval_returns_old_snapshot(self):
        svc = DecisionSnapshotService(interval_s=30.0)
        svc.update("BTCUSDT", now_ms=1000, decision={"opportunity_score": 80.0})
        snap = svc.update("BTCUSDT", now_ms=2000, decision={"opportunity_score": 95.0})
        # 周期未到：仍返回冻结时的旧值（§11 稳定，不秒级重排）
        assert snap["decision"]["opportunity_score"] == 80.0
        assert snap["frozen_at"] == 1000

    def test_after_interval_refrozen(self):
        svc = DecisionSnapshotService(interval_s=30.0)
        svc.update("BTCUSDT", now_ms=1000, decision={"opportunity_score": 80.0})
        snap = svc.update("BTCUSDT", now_ms=31_000, decision={"opportunity_score": 95.0})
        assert snap["decision"]["opportunity_score"] == 95.0
        assert snap["frozen_at"] == 31_000

    def test_symbols_independent(self):
        svc = DecisionSnapshotService(interval_s=30.0)
        svc.update("BTCUSDT", now_ms=1000, decision={"a": 1})
        svc.update("ETHUSDT", now_ms=1000, decision={"a": 2})
        assert svc.get("SOLUSDT") is None
        assert svc.get("BTCUSDT")["decision"]["a"] == 1
        assert svc.get("ETHUSDT")["decision"]["a"] == 2

    def test_all_returns_copy(self):
        svc = DecisionSnapshotService(interval_s=30.0)
        svc.update("BTCUSDT", now_ms=1000, decision={"a": 1})
        all_snaps = svc.all()
        assert set(all_snaps) == {"BTCUSDT"}
        all_snaps["BTCUSDT"]["decision"]["a"] = 99
        assert svc.get("BTCUSDT")["decision"]["a"] == 1


class TestRestore:
    def test_restore_for_restart(self):
        """§48 重启恢复。"""
        svc = DecisionSnapshotService(interval_s=30.0)
        svc.restore("BTCUSDT", frozen_at=5000, snapshot={"opportunity_score": 77.0})
        snap = svc.get("BTCUSDT")
        assert snap["frozen_at"] == 5000
        assert snap["decision"]["opportunity_score"] == 77.0

    def test_restored_snapshot_respected_next_cycle(self):
        svc = DecisionSnapshotService(interval_s=30.0)
        svc.restore("BTCUSDT", frozen_at=5000, snapshot={"opportunity_score": 77.0})
        # 周期未到（5000→9000 < 30s）→ 仍返回恢复值
        snap = svc.update("BTCUSDT", now_ms=9000, decision={"opportunity_score": 66.0})
        assert snap["decision"]["opportunity_score"] == 77.0