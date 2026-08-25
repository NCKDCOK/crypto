"""§66.6 Position Monitoring — MFE / MAE / TP1–TP3 / Stop / Withdrawal Exit 正确。"""

from __future__ import annotations

import pytest

from src.config import SimulationConfig
from src.simulation import PaperPositionManager, SimulationQueueItem
from src.simulation.position import PaperPosition


def _pos(mgr, snapshot_dict, entry_price=100.0, now_ms=5000) -> PaperPosition:
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


def _mgr(**cfg_over) -> PaperPositionManager:
    cfg = SimulationConfig(**cfg_over)
    return PaperPositionManager(cfg)


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


class TestMfeMae:
    def test_mfe_tracks_best_move(self, snapshot_dict):
        mgr = _mgr()
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(102.0), 6000)   # +2%
        mgr.tick(p, _ctx(99.0), 7000)    # -1%
        mgr.tick(p, _ctx(103.0), 8000)   # +3%
        assert p.mfe_pct == pytest.approx(3.0, abs=1e-6)
        assert p.mae_pct == pytest.approx(-1.0, abs=1e-6)

    def test_mae_tracks_worst_move(self, snapshot_dict):
        mgr = _mgr()
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(97.0), 6000)    # -3%
        mgr.tick(p, _ctx(104.0), 7000)   # +4%
        assert p.mfe_pct == pytest.approx(4.0, abs=1e-6)
        assert p.mae_pct == pytest.approx(-3.0, abs=1e-6)

    def test_short_pnl_sign_inverted(self, snapshot_dict):
        snap = dict(snapshot_dict, direction="SHORT")
        mgr = _mgr()
        p = _pos(mgr, snap, entry_price=100.0)
        mgr.tick(p, _ctx(98.0), 6000)    # 空头 +2%
        assert p.current_pnl_pct == pytest.approx(2.0, abs=1e-6)
        mgr.tick(p, _ctx(103.0), 7000)   # 空头 -3%
        assert p.current_pnl_pct == pytest.approx(-3.0, abs=1e-6)
        assert p.mae_pct == pytest.approx(-3.0, abs=1e-6)


class TestStaticTargets:
    def test_tp1_hit_closes(self, snapshot_dict):
        mgr = _mgr()
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        ev = mgr.tick(p, _ctx(110.0), 6000)  # tp1=110
        assert p.status == "CLOSED"
        assert p.exit_reason == "TP1_HIT"
        assert p.tp1_hit is True
        assert ev["new_status"] == "CLOSED"

    def test_tp2_tp3_flags_recorded(self, snapshot_dict):
        """价格越过 TP1 即静态平仓，TP2/TP3 标志在触及价时记录。"""
        mgr = _mgr()
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        # 直接跳空到 125：TP1(110)/TP2(120) 触及 → 平仓并记录 1/2 标志
        ev = mgr.tick(p, _ctx(125.0), 6000)
        assert ev is not None and p.status == "CLOSED"
        assert p.tp1_hit is True and p.tp2_hit is True
        assert p.tp3_hit is False
        # 新仓直接到 131 → 三档全标志
        p2 = _pos(mgr, snapshot_dict, entry_price=100.0, now_ms=6000)
        mgr.tick(p2, _ctx(131.0), 7000)
        assert p2.tp1_hit is True and p2.tp2_hit is True and p2.tp3_hit is True

    def test_stop_hit_closes(self, snapshot_dict):
        mgr = _mgr()
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        ev = mgr.tick(p, _ctx(90.0), 6000)  # invalidation_price=90
        assert p.status == "CLOSED"
        assert p.exit_reason == "INVALIDATION_HIT"
        assert p.stop_hit is True
        assert ev["new_status"] == "CLOSED"

    def test_in_zone_does_not_close(self, snapshot_dict):
        mgr = _mgr()
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        ev = mgr.tick(p, _ctx(104.0), 6000)  # 在 Zone/TP 之下
        assert p.status == "OPEN"
        assert ev is None


class TestWithdrawalExit:
    def test_withdrawal_needs_confirmation_window(self, snapshot_dict):
        """§31/§66.6：Withdrawal 需连续确认 withdrawal_confirm_s 才退出。"""
        mgr = _mgr(withdrawal_confirm_s=30.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        # 第一次触发：开始计时，不平仓
        ev = mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6000)
        assert p.status == "OPEN"
        assert ev is None
        assert p.withdrawal_since == 6000
        # 窗口未满（< 30s）
        ev = mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6_001)
        assert p.status == "OPEN"
        # 窗口满（from 6000 + 30s）→ SIGNAL_WITHDRAWAL
        ev = mgr.tick(p, _ctx(101.0, withdrawal_active=True), 36_001)
        assert p.status == "CLOSED"
        assert p.exit_reason == "SIGNAL_WITHDRAWAL"
        assert p.exit_is_dynamic is True
        assert ev["reason"] == "SIGNAL_WITHDRAWAL"

    def test_withdrawal_clears_when_signal_gone(self, snapshot_dict):
        mgr = _mgr(withdrawal_confirm_s=30.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6000)
        assert p.withdrawal_since == 6000
        mgr.tick(p, _ctx(101.0, withdrawal_active=False), 7000)
        assert p.withdrawal_since is None
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 40_000)
        assert p.status == "OPEN"  # 计数器重置，再次从 40000 开始


class TestDistributionExit:
    def test_distribution_risk_exit_after_confirm(self, snapshot_dict):
        mgr = _mgr(distribution_risk_threshold=65.0, distribution_confirm_s=30.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, distribution_risk=70.0), 6000)
        assert p.status == "OPEN"
        assert p.distribution_since == 6000
        ev = mgr.tick(p, _ctx(101.0, distribution_risk=70.0), 36_001)
        assert p.status == "CLOSED"
        assert p.exit_reason == "DISTRIBUTION_EXIT"
        assert p.exit_is_dynamic is True

    def test_low_distribution_risk_no_exit(self, snapshot_dict):
        mgr = _mgr(distribution_risk_threshold=65.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        ev = mgr.tick(p, _ctx(101.0, distribution_risk=40.0), 6000)
        assert p.status == "OPEN"
        assert ev is None


class TestDirectionFlipExit:
    def test_direction_flip_exit_after_confirm(self, snapshot_dict):
        mgr = _mgr(direction_flip_confirm_s=20.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, direction="SHORT"), 6000)
        assert p.flip_since == 6000
        ev = mgr.tick(p, _ctx(101.0, direction="SHORT"), 26_001)
        assert p.status == "CLOSED"
        assert p.exit_reason == "DIRECTION_FLIP"

    def test_neutral_direction_no_flip(self, snapshot_dict):
        mgr = _mgr(direction_flip_confirm_s=20.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        ev = mgr.tick(p, _ctx(101.0, direction="NEUTRAL"), 6000)
        assert p.status == "OPEN"
        assert ev is None


class TestInvalidationExit:
    def test_invalidated_ctx_exits_after_confirm(self, snapshot_dict):
        mgr = _mgr(invalidation_confirm_s=5.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, invalidated=True), 6000)
        assert p.status == "OPEN"
        assert p.invalidated_since == 6000
        ev = mgr.tick(p, _ctx(101.0, invalidated=True), 11_001)
        assert p.status == "CLOSED"
        assert p.exit_reason == "INVALIDATION_HIT"

    def test_invalidated_without_price_move(self, snapshot_dict):
        mgr = _mgr(invalidation_confirm_s=5.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(None, invalidated=True), 6000)
        mgr.tick(p, _ctx(None, invalidated=True), 11_001)
        assert p.status == "CLOSED"
        assert p.exit_reason == "INVALIDATION_HIT"


class TestTimeExpired:
    def test_open_beyond_static_track_max_hours(self, snapshot_dict):
        mgr = _mgr(static_track_max_hours=0.001)  # 3.6s
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        # 3.6s 内无 TP/Stop/动态退出 → TIME_EXPIRED
        ev = mgr.tick(p, _ctx(101.0), 100_000)
        assert p.status == "CLOSED"
        assert p.exit_reason == "TIME_EXPIRED"
        assert ev["reason"] == "TIME_EXPIRED"


class TestResult:
    def test_result_persisted_after_static_close(self, snapshot_dict):
        mgr = _mgr()
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        ev = mgr.tick(p, _ctx(110.0), 6000)  # TP1 静态平仓，非动态 → 立即定稿
        assert p.result_persisted is True
        assert ev is not None and ev["new_status"] == "CLOSED"

    def test_result_not_persisted_while_static_tracking(self, snapshot_dict):
        """动态退出后仍在静态跟踪 → 结果不定稿（§32B 双记录）。"""
        mgr = _mgr(withdrawal_confirm_s=1.0)
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 6_000)
        mgr.tick(p, _ctx(101.0, withdrawal_active=True), 7_001)  # 动态退出
        assert p.status == "CLOSED"
        assert p.exit_is_dynamic is True
        assert p.static_tracking is True
        assert p.result_persisted is False


class TestPositionDictRoundtrip:
    def test_to_dict_from_dict(self, snapshot_dict):
        mgr = _mgr()
        p = _pos(mgr, snapshot_dict, entry_price=100.0)
        d = p.to_dict()
        p2 = PaperPosition.from_dict(d)
        assert p2.simulation_id == p.simulation_id
        assert p2.entry_price == p.entry_price
        assert p2.tp1 == p.tp1
        assert p2.status == "OPEN"