"""§66.4 Simulation Queue — 正式推荐自动加入；COOLDOWN / SUSPECTED_START 不加入；状态机往返。"""

from __future__ import annotations

import pytest

from src.config import SimulationConfig
from src.simulation import (
    EntryRevalidationEngine,
    PaperPositionManager,
    SimulationQueueManager,
    SimulationStatus,
)


def _queue_and_deps(snapshot_dict, **cfg_over):
    """构造 queue + position + revalidation 全链（runtime 接线形态）。"""
    cfg = SimulationConfig(**cfg_over)
    reval = EntryRevalidationEngine()
    positions = PaperPositionManager(cfg)
    repo = None
    q = SimulationQueueManager(cfg, repo, revalidation=reval, positions=positions)
    return q, cfg, reval, positions


class TestAutoEnqueue:
    """§66.4：正式推荐自动加入 WATCHING。"""

    def test_formal_recommendation_joins(self, snapshot_dict):
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        item = q.create_from_snapshot(snapshot_dict, now_ms=1000)
        assert item.status == SimulationStatus.WATCHING
        assert item.simulation_id == snapshot_dict["snapshot_id"]
        assert item.entry_zone_low == 95.0
        assert item.entry_zone_high == 105.0
        assert item.recommendation_price == 100.0
        assert q.get(item.simulation_id) is item

    def test_enqueued_records_distance(self, snapshot_dict):
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        item = q.create_from_snapshot(snapshot_dict, now_ms=1000)
        # 推荐价 100，Zone 中心 100 → 距离 0%
        assert item.distance_pct is not None
        assert abs(item.distance_pct) < 1e-9


class TestSnapshotEnqueue:
    """V1.4 §2：正式推荐 → 不可变快照 → WATCHING 入队（绑定 recommendation_id）。

    门槛由 RecommendationGate（§三）判定（见 test_recommendation_gate.py），不再由
    快照服务判定。runtime 保证只有已发布正式推荐才创建快照（无 rec_id 禁止进模拟）。
    """

    def test_create_from_snapshot_binds_recommendation_id(self, snapshot_dict):
        snap = {**snapshot_dict, "recommendation_id": "REC-ABC123"}
        q, _, _, _ = _queue_and_deps(snap)
        item = q.create_from_snapshot(snap, now_ms=1000)
        assert item.recommendation_id == "REC-ABC123"
        assert item.status == SimulationStatus.WATCHING

    def test_simulation_id_equals_snapshot_id(self, snapshot_dict):
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        item = q.create_from_snapshot(snapshot_dict, now_ms=1000)
        assert item.simulation_id == snapshot_dict["snapshot_id"]
        assert item.snapshot_id == snapshot_dict["snapshot_id"]


class TestWatchingTransitions:
    def test_watching_invalidation_when_state_leaves_formal(self, snapshot_dict, good_ctx):
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        ctx = dict(good_ctx, state="EXHAUSTION")
        events = q.tick_symbol("BTCUSDT", ctx, now_ms=2000)
        assert len(events) == 1
        assert events[0]["new_status"] == SimulationStatus.INVALIDATED.value
        assert q.get("BTCUSDT-20250101-001").status == SimulationStatus.INVALIDATED

    def test_watching_expires_after_config(self, snapshot_dict, good_ctx):
        q, _, _, _ = _queue_and_deps(snapshot_dict, recommendation_expire_minutes=0.001)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        # expire_ms = 60ms；now - created > 60 → EXPIRED
        events = q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=2000)
        assert events and events[0]["new_status"] == SimulationStatus.EXPIRED.value

    def test_watching_zone_reached(self, snapshot_dict, good_ctx):
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        events = q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=2000)
        item = q.get("BTCUSDT-20250101-001")
        assert item.status == SimulationStatus.ENTRY_ZONE_REACHED
        assert item.entry_zone_reached_at == 2000
        assert item.entry_zone_reached_price == 100.0

    def test_watching_records_24h_basics(self, snapshot_dict, good_ctx):
        """§24：WATCHING 持续记录当前价/最大涨跌。"""
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        item = q.create_from_snapshot(snapshot_dict, now_ms=1000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=102.0), now_ms=2000)   # +2%
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=98.0), now_ms=3000)    # -2%
        assert item.current_price == 98.0
        assert item.max_gain_pct == pytest.approx(2.0, abs=1e-6)
        assert item.max_drawdown_pct == pytest.approx(-2.0, abs=1e-6)


class TestFullHappyPath:
    """正式推荐 → WATCHING → Zone → REVALIDATING → ARMED → 成交 → OPEN → CLOSED（§23 主线）。"""

    def test_complete_flow_to_simulated_entry(self, snapshot_dict, good_ctx):
        q, _, _, positions = _queue_and_deps(snapshot_dict)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        sim_id = snapshot_dict["snapshot_id"]

        # 1 tick: 进 Zone → ENTRY_ZONE_REACHED
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=2000)
        assert q.get(sim_id).status == SimulationStatus.ENTRY_ZONE_REACHED

        # 2 tick: → REVALIDATING
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=3000)
        item = q.get(sim_id)
        assert item.status == SimulationStatus.REVALIDATING
        assert item.revalidate_started_at == 3000

        # 3 tick: 十项全过 → ARMED
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=4000)
        item = q.get(sim_id)
        assert item.status == SimulationStatus.ARMED
        assert item.armed_at == 4000
        assert item.revalidate_result["passed"] is True

        # 4 tick: 仍在 Zone 且验证通过 → 成交（§28）
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=5000)
        item = q.get(sim_id)
        assert item.status == SimulationStatus.SIMULATED_ENTRY
        assert item.entered_at == 5000
        assert item.entry_price == 100.0
        assert "Revalidation" in (item.entry_reason or "")

        # 5 tick: → OPEN，且持仓已建立
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=6000)
        assert q.get(sim_id).status == SimulationStatus.OPEN
        pos = positions.get(sim_id)
        assert pos is not None and pos.status == "OPEN"
        assert pos.entry_price == 100.0


class TestRevalidationCancel:
    def test_zone_with_withdrawal_cancels(self, snapshot_dict, good_ctx):
        """§66.5：到 Zone + Withdrawal → CANCELLED。"""
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        sim_id = snapshot_dict["snapshot_id"]
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=2000)   # → ENTRY_ZONE_REACHED
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=3000)   # → REVALIDATING
        events = q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0, withdrawal_active=True), now_ms=4000)
        item = q.get(sim_id)
        assert item.status == SimulationStatus.CANCELLED
        assert events[0]["new_status"] == "CANCELLED"
        assert item.fail_reason and "Withdrawal" in item.fail_reason

    def test_stale_data_cancels_no_entry(self, snapshot_dict, good_ctx):
        """§66.5：数据 stale → 不入场。"""
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        sim_id = snapshot_dict["snapshot_id"]
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=2000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=3000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0, data_age_ms=120_000), now_ms=4000)
        assert q.get(sim_id).status == SimulationStatus.CANCELLED

    def test_armed_invalidation_before_fill(self, snapshot_dict, good_ctx):
        """ARMED 期状态失效 → INVALIDATED。"""
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        sim_id = snapshot_dict["snapshot_id"]
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=2000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=3000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=4000)
        assert q.get(sim_id).status == SimulationStatus.ARMED
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=95.5, state="WITHDRAWAL"), now_ms=5000)
        assert q.get(sim_id).status == SimulationStatus.INVALIDATED


class TestMissedGrace:
    def test_armed_out_of_zone_misses_after_grace(self, snapshot_dict, good_ctx):
        q, cfg, _, _ = _queue_and_deps(snapshot_dict, entry_zone_grace_s=0.01)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        sim_id = snapshot_dict["snapshot_id"]
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=2000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=3000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=4000)
        assert q.get(sim_id).status == SimulationStatus.ARMED
        # 价格离开 Zone（如 110.0 > high 105），且验证仍过 → 宽限期后 MISSED
        # 宽限 0.01s=10ms；armed_at=4000，5000 起已超 → 离开 Zone 的 tick 即 MISSED
        events = q.tick_symbol("BTCUSDT", dict(good_ctx, price=110.0), now_ms=5_000)
        assert q.get(sim_id).status == SimulationStatus.MISSED
        assert events and events[0]["new_status"] == "MISSED"


class TestQueueClosedAfterPosition:
    def test_open_to_closed_when_position_finalized(self, snapshot_dict, good_ctx):
        """OPEN 后持仓 CLOSED → 队列 CLOSED。"""
        q, cfg, _, positions = _queue_and_deps(snapshot_dict)
        q.create_from_snapshot(snapshot_dict, now_ms=1000)
        sim_id = snapshot_dict["snapshot_id"]
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=2000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=3000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=4000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=5000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=100.0), now_ms=6000)
        assert q.get(sim_id).status == SimulationStatus.OPEN
        # 触发 TP1（LONG tp1=110）→ 静态平仓；运行时按 symbol 先驱动持仓再驱动队列
        positions.tick_symbol("BTCUSDT", dict(good_ctx, price=111.0), now_ms=7000)
        q.tick_symbol("BTCUSDT", dict(good_ctx, price=111.0), now_ms=7000)
        pos = positions.get(sim_id)
        assert pos.status == "CLOSED"
        assert pos.exit_reason == "TP1_HIT"
        assert q.get(sim_id).status == SimulationStatus.CLOSED
        assert q.get(sim_id).exit_reason == "TP1_HIT"

    def test_restore_item(self, snapshot_dict):
        """§48 重启恢复：restore 与 tick 一致。"""
        q, _, _, _ = _queue_and_deps(snapshot_dict)
        item = q.create_from_snapshot(snapshot_dict, now_ms=1000)
        d = item.to_dict()
        q2, _, _, _ = _queue_and_deps(snapshot_dict)
        q2.restore_item(d)
        restored = q2.get(item.simulation_id)
        assert restored is not None
        assert restored.status == SimulationStatus.WATCHING
        assert restored.snapshot_id == snapshot_dict["snapshot_id"]