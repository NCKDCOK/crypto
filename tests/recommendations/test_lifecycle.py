"""V1.4 §六 RecommendationLifecycleEngine 测试（§四十二 Supervisor 组）。

依据：crypto_radar_v1.4_fix_update_plan.md §六（Supervisor 真正接管发布后的机会）/
§八（状态滞回）/ §三十三（最低驻留 + score 抖动不让推荐消失）/ §四.4（Hard Veto /
Invalidation 即时退出）/ §二十七（published_* 冻结）。

覆盖 §四十二「Supervisor」组：
- minimum residence 生效（min lifetime 内不普通退出）
- 连续失败才普通降级（streak >= 阈值）
- Hard Veto 即时退出
- Withdrawal 即时退出
- Invalidation / Data Critical 即时退出（§四.4 / §三十三例外）
- score 抖动 → WEAKENING 不删除（§三十三）
- 风险池 → RISK 仍活跃（§七.6）
- published_* 冻结、current_* 持续更新（§二十七）
- 终态不再转移
"""

from __future__ import annotations

from src.config import RecommendationConfig
from src.recommendations import (
    LifecycleContext,
    PublishedRecommendation,
    RecommendationLifecycleEngine,
    RecommendationStatus,
)


def _cfg() -> RecommendationConfig:
    return RecommendationConfig()  # min_lifetime=300s, downgrade_streak=2, min_opp=70


def _engine() -> RecommendationLifecycleEngine:
    return RecommendationLifecycleEngine(_cfg())


def _rec(*, published_at: int = 0, opp: float = 80.0,
         side: str = "LONG", inv: float = 95.0) -> PublishedRecommendation:
    return PublishedRecommendation(
        recommendation_id="REC-TEST", symbol="BTCUSDT",
        created_at=published_at, published_at=published_at,
        side=side, setup_type="ACCUMULATION", primary_timeframe="15m",
        published_state="START_CONFIRMED", current_state="START_CONFIRMED",
        published_price=105.0, current_price=105.0,
        published_opportunity_score=opp,
        published_signal_confirmation=80.0, published_data_confidence=90.0,
        current_opportunity_score=opp,
        current_signal_confirmation=80.0, current_data_confidence=90.0,
        entry_zone_low=100.0, entry_zone_high=110.0, invalidation_price=inv,
        tp1=120.0, tp2=130.0, tp3=140.0, rr1=2.0, rr2=3.0, rr3=4.0,
        status=RecommendationStatus.PUBLISHED, risk_status="NORMAL",
        confirmation_level="STANDARD", updated_at=published_at,
    )


def _ctx(now: int, *, state: str = "START_CONFIRMED", opp: float = 80.0,
         price: float = 105.0, **over) -> LifecycleContext:
    kw = dict(
        now_ms=now, current_price=price, current_state=state,
        current_opportunity_score=opp, current_signal_confirmation=80.0,
        current_data_confidence=90.0,
        hard_veto=False, withdrawal_active=False, invalidated=False,
        data_critical=False, risk_status=None,
        in_formal_range=state in ("START_CONFIRMED", "CONTINUATION"),
    )
    kw.update(over)
    return LifecycleContext(**kw)


class TestNormalAndWeakening:
    def test_first_tick_goes_monitoring(self):
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000))
        assert rec.status == RecommendationStatus.MONITORING
        assert d.transitioned is True
        assert d.new_status == RecommendationStatus.MONITORING
        assert d.exited is False

    def test_current_updated_published_frozen(self):
        """§二十七：current_* 持续更新，published_* 冻结不可变。"""
        eng = _engine()
        rec = _rec(opp=80.0)
        eng.register(rec, 0)
        eng.tick(rec, _ctx(now=1_000, opp=72.0, price=108.0))
        assert rec.current_opportunity_score == 72.0
        assert rec.current_price == 108.0
        assert rec.published_opportunity_score == 80.0   # 冻结
        assert rec.published_price == 105.0               # 冻结

    def test_score_jitter_goes_weakening_not_removed(self):
        """§三十三：Opportunity 跌破门禁门槛（< 70）但仍正式范围 → WEAKENING，不删除。"""
        eng = _engine()
        rec = _rec(opp=80.0)
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000, opp=65.0))   # 65 < 70
        assert rec.status == RecommendationStatus.WEAKENING
        assert rec.is_active() is True                 # 仍在首页活跃区
        assert d.exited is False

    def test_repeated_weakening_stays_active(self):
        eng = _engine()
        rec = _rec(opp=80.0)
        eng.register(rec, 0)
        for t in (1_000, 2_000, 3_000):
            eng.tick(rec, _ctx(now=t, opp=66.0))
        assert rec.status == RecommendationStatus.WEAKENING
        assert rec.is_active() is True


class TestImmediateExits:
    def test_hard_veto_immediate_exit(self):
        """§四十二：Hard Veto 即时退出（无视滞回 / 驻留）。"""
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000, hard_veto=True))
        assert rec.status == RecommendationStatus.INVALIDATED
        assert rec.exit_reason == "HARD_VETO"
        assert rec.closed_at == 1_000
        assert d.exited is True
        assert rec.is_active() is False

    def test_hard_veto_bypasses_min_lifetime(self):
        """§四.4：Hard Veto 在最低驻留期内也即时退出。"""
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000, hard_veto=True))   # 1s < 300s
        assert d.exited is True
        assert rec.status == RecommendationStatus.INVALIDATED

    def test_withdrawal_immediate_exit(self):
        """§四十二：Withdrawal 即时退出。"""
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000, state="WITHDRAWAL",
                              withdrawal_active=True, in_formal_range=False))
        assert rec.status == RecommendationStatus.EXITED
        assert rec.exit_reason == "SIGNAL_WITHDRAWAL"
        assert d.exited is True

    def test_invalidation_immediate_exit(self):
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000, invalidated=True))
        assert rec.status == RecommendationStatus.INVALIDATED
        assert rec.exit_reason == "INVALIDATION_HIT"
        assert d.exited is True

    def test_data_critical_immediate_exit(self):
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000, data_critical=True))
        assert rec.status == RecommendationStatus.EXITED
        assert rec.exit_reason == "DATA_CRITICAL"
        assert d.exited is True


class TestRiskPool:
    def test_exhaustion_goes_risk_still_active(self):
        """§七.6：EXHAUSTION → RISK（仍活跃，首页风险提醒）。"""
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000, state="EXHAUSTION",
                              risk_status="EXHAUSTION", in_formal_range=False))
        assert rec.status == RecommendationStatus.RISK
        assert rec.risk_status == "EXHAUSTION"
        assert rec.is_active() is True
        assert d.exited is False

    def test_leaving_risk_back_to_monitoring(self):
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        eng.tick(rec, _ctx(now=1_000, state="EXHAUSTION",
                           risk_status="EXHAUSTION", in_formal_range=False))
        d = eng.tick(rec, _ctx(now=2_000, state="START_CONFIRMED"))
        assert rec.status == RecommendationStatus.MONITORING
        assert rec.risk_status is None


class TestOrdinaryDowngradeHysteresis:
    """§八：连续 N 次离开正式范围才普通降级；§三十三：min lifetime 内不退出。"""

    def test_minimum_residence_blocks_ordinary_exit(self):
        """§四十二：min lifetime 内离开正式范围 → WEAKENING（不退出）。"""
        eng = _engine()
        rec = _rec(published_at=0)
        eng.register(rec, 0)
        # now=100s < 300s min lifetime，state 退到 COOLDOWN（非 withdrawal）
        d = eng.tick(rec, _ctx(now=100_000, state="COOLDOWN", in_formal_range=False))
        assert rec.status == RecommendationStatus.WEAKENING
        assert d.exited is False
        assert rec.is_active() is True

    def test_consecutive_failures_then_ordinary_exit(self):
        """§四十二：连续失败才普通降级（past min lifetime，streak >= 阈值）。"""
        eng = _engine()
        rec = _rec(published_at=0)
        eng.register(rec, 0)
        # past min lifetime（now=400s > 300s）
        d1 = eng.tick(rec, _ctx(now=400_000, state="COOLDOWN", in_formal_range=False))
        assert rec.status == RecommendationStatus.WEAKENING   # 1/2，未退出
        assert d1.exited is False
        d2 = eng.tick(rec, _ctx(now=410_000, state="COOLDOWN", in_formal_range=False))
        assert rec.status == RecommendationStatus.EXITED        # 2/2，普通降级退出
        assert rec.exit_reason == "STATE_EXIT"
        assert d2.exited is True

    def test_single_failure_past_lifetime_not_enough(self):
        eng = _engine()
        rec = _rec(published_at=0)
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=400_000, state="COOLDOWN", in_formal_range=False))
        assert d.exited is False
        assert rec.status == RecommendationStatus.WEAKENING

    def test_recover_resets_fail_streak(self):
        """核心条件恢复 → streak 清零，需重新累计。"""
        eng = _engine()
        rec = _rec(published_at=0)
        eng.register(rec, 0)
        eng.tick(rec, _ctx(now=400_000, state="COOLDOWN", in_formal_range=False))  # streak=1
        eng.tick(rec, _ctx(now=410_000, state="START_CONFIRMED"))                 # 恢复 → 清零
        d = eng.tick(rec, _ctx(now=420_000, state="COOLDOWN", in_formal_range=False))  # streak=1 again
        assert d.exited is False
        assert rec.status == RecommendationStatus.WEAKENING


class TestTerminalAndRegister:
    def test_terminal_rec_no_transition(self):
        eng = _engine()
        rec = _rec()
        rec.status = RecommendationStatus.EXITED
        rec.exit_reason = "SIGNAL_WITHDRAWAL"
        rec.closed_at = 123
        eng.register(rec, 0)
        d = eng.tick(rec, _ctx(now=1_000, hard_veto=True))   # 终态：不再转移
        assert d.transitioned is False
        assert d.exited is False
        assert rec.exit_reason == "SIGNAL_WITHDRAWAL"        # 不被覆盖

    def test_auto_register_on_first_tick(self):
        """未显式 register → tick 自动注册。"""
        eng = _engine()
        rec = _rec()
        d = eng.tick(rec, _ctx(now=1_000))   # 未 register
        assert rec.status == RecommendationStatus.MONITORING
        assert d.transitioned is True
        assert eng.get_track(rec.recommendation_id) is not None

    def test_forget_clears_track(self):
        eng = _engine()
        rec = _rec()
        eng.register(rec, 0)
        assert eng.get_track(rec.recommendation_id) is not None
        eng.forget(rec.recommendation_id)
        assert eng.get_track(rec.recommendation_id) is None
