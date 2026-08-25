"""§66.7 Static vs Dynamic Exit — 必须同时记录系统动态退出与固定计划结果。"""

from __future__ import annotations

import pytest

from src.config import SimulationConfig
from src.simulation import PaperPositionManager, SimulationQueueItem


def _open_position(mgr, snapshot_dict, entry_price=100.0, now_ms=5000):
    item = SimulationQueueItem(
        simulation_id=snapshot_dict["snapshot_id"],
        snapshot_id=snapshot_dict["snapshot_id"],
        symbol=snapshot_dict["symbol"],
        snapshot=dict(snapshot_dict),
        created_at=1000,
        updated_at=now_ms,
        entered_at=now_ms,
        entry_price=entry_price,
        entry_reason="Entry Zone 内第一笔符合 Revalidation 的价格（§28）",
        entry_confirmation={"passed": True},
    )
    return mgr.open(item, now_ms)


def _ctx(price, **over):
    ctx = {
        "price": price,
        "state": "START_CONFIRMED",
        "direction": "LONG",
        "withdrawal_active": False,
        "invalidated": False,
        "distribution_risk": 20.0,
        "pump_risk": 10.0,
    }
    ctx.update(over)
    return ctx


class TestDynamicExitThenStaticTracking:
    """§32 + 用户决策：动态退出后后台静态跟踪直到触及原 TP1 或失效（孰先），24h 上限。"""

    def test_dynamic_withdrawal_then_static_tp1(self, snapshot_dict):
        """动态撤离退出 → 静态跟踪 → 触及原 TP1 → static_outcome=TP1_HIT。"""
        cfg = SimulationConfig(withdrawal_confirm_s=1.0, static_track_max_hours=24.0)
        mgr = PaperPositionManager(cfg)
        p = _open_position(mgr, snapshot_dict, entry_price=100.0)

        # 动态退出：Withdrawal 确认（1s）
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6_000)
        ev = mgr.tick(p, _ctx(101.0, withdrawal_active=True), 7_001)
        assert p.status == "CLOSED"
        assert p.exit_is_dynamic is True
        assert p.exit_reason == "SIGNAL_WITHDRAWAL"

        # 此时静态跟踪激活，结果未定稿
        assert p.static_tracking is True
        assert p.result_persisted is False

        # 静态跟踪：价格触及原 TP1=110 → 静态结局 TP1
        ev2 = mgr.tick(p, _ctx(111.0), 100_000)
        assert p.static_outcome == "TP1_HIT"
        assert p.static_tracking is False
        assert p.result_persisted is True
        assert p.static_exit_price == 111.0
        assert p.static_pnl_pct == pytest.approx(11.0, abs=1e-6)
        assert ev2["new_status"] == "STATIC_DONE"

    def test_dynamic_withdrawal_then_static_stop(self, snapshot_dict):
        """动态退出后静态跟踪触及原 Stop → static_outcome=STOP_HIT。"""
        cfg = SimulationConfig(withdrawal_confirm_s=1.0, static_track_max_hours=24.0)
        mgr = PaperPositionManager(cfg)
        p = _open_position(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6_000)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 7_001)
        assert p.static_tracking is True

        mgr.tick(p, _ctx(89.0), 100_000)  # ≤ invalidation_price=90
        assert p.static_outcome == "STOP_HIT"
        assert p.static_tracking is False
        assert p.result_persisted is True
        assert p.static_pnl_pct == pytest.approx(-11.0, abs=1e-6)

    def test_dynamic_then_static_timeout(self, snapshot_dict):
        """动态退出后 24h（静态跟踪上限）未触 TP/Stop → static_outcome=TIME_EXPIRED。"""
        cfg = SimulationConfig(withdrawal_confirm_s=1.0, static_track_max_hours=24.0)
        mgr = PaperPositionManager(cfg)
        p = _open_position(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6_000)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 7_001)
        assert p.static_tracking is True
        until = p.static_track_until_ms
        assert until == 7_001 + 24 * 3_600_000

        # 24h 到点仍未触 TP/Stop（当前价 103 在 90~110 之间）
        ev = mgr.tick(p, _ctx(103.0), until + 1)
        assert p.static_outcome == "TIME_EXPIRED"
        assert p.static_tracking is False
        assert p.result_persisted is True
        assert ev["new_status"] == "STATIC_DONE"

    def test_dynamic_then_invalidated_static(self, snapshot_dict):
        """动态退出后状态失效 → static_outcome=INVALIDATED。"""
        cfg = SimulationConfig(withdrawal_confirm_s=1.0, static_track_max_hours=24.0)
        mgr = PaperPositionManager(cfg)
        p = _open_position(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6_000)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 7_001)
        assert p.static_tracking is True

        mgr.tick(p, _ctx(102.0, invalidated=True), 50_000)
        assert p.static_outcome == "INVALIDATED"
        assert p.invalidated is True
        assert p.static_tracking is False
        assert p.result_persisted is True


class TestBothRecordedInResult:
    """§32：最终结果必须同时携带动态退出价与静态计划结局，供「跟资金撤 vs 跟固定计划」比较。"""

    def test_result_contains_dynamic_and_static(self, snapshot_dict):
        cfg = SimulationConfig(withdrawal_confirm_s=1.0, static_track_max_hours=24.0)
        mgr = PaperPositionManager(cfg)
        p = _open_position(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6_000)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 7_001)   # 动态退出 @101
        mgr.tick(p, _ctx(111.0), 100_000)                          # 静态 TP1 @111
        assert p.result_persisted is True

        res = mgr._build_result(p)
        # A. 动态退出（§32A）
        assert res["exit_reason"] == "SIGNAL_WITHDRAWAL"
        assert res["dynamic_exit_price"] == 101.0
        # B. 静态计划（§32B）
        sp = res["static_plan_result"]
        assert sp["outcome"] == "TP1_HIT"
        assert sp["static_exit_price"] == 111.0
        assert sp["tp1_hit"] is True
        assert sp["tp2_hit"] is False
        assert sp["stop_hit"] is False
        assert sp["duration_hours"] == pytest.approx((100_000 - 5000) / 3_600_000, abs=1e-3)

    def test_static_only_close_has_no_dynamic_price(self, snapshot_dict):
        """纯静态 TP1 平仓：exit_is_dynamic=False → dynamic_exit_price=None。"""
        cfg = SimulationConfig()
        mgr = PaperPositionManager(cfg)
        p = _open_position(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(110.0), 6_000)
        assert p.exit_is_dynamic is False
        res = mgr._build_result(p)
        assert res["dynamic_exit_price"] is None
        assert res["static_plan_result"]["outcome"] == "TP1_HIT"

    def test_dynamic_reason_set(self, snapshot_dict):
        """动态退出原因集合覆盖 §31 资金撤离 4 + 手动。"""
        from src.simulation.position import _DYNAMIC_REASONS
        assert {"SIGNAL_WITHDRAWAL", "DISTRIBUTION_EXIT", "DIRECTION_FLIP",
                "INVALIDATION_HIT", "MANUAL_CLOSE"} == _DYNAMIC_REASONS