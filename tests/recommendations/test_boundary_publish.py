"""V1.4 §四 5m 决策边界 + §九/§三十四 发布去重/冷却 测试（§四十二 Recommendation 组）。

依据：crypto_radar_v1.4_fix_update_plan.md §四（5m 决策边界）/ §九（首页不再强行 Top10）/
§二十七（快照不可变）/ §三十三（最低驻留）/ §三十四（同向同 Setup 冷却）。

覆盖 §四十二「Recommendation」组中由 runtime 编排决定的行为：
- 5m 未收盘 / 无 5m bar → 不得正式发布（runtime 边界）
- 突破类 Setup 未 5m 收盘确认突破 → 不得发布
- 同一 5m 收盘窗口只评估一次（_last_gate_bar 去重）
- 5m 收盘 + 通过 Gate → 发布正式推荐（PUBLISHED）
- 同 symbol 活跃期间不得重复发布（§九）
- 同 symbol+方向+Setup 30m 冷却（§三十四）；冷却过期 / 换 Setup 可重发
- 无机会时正式推荐仓库为空（§十 数据源）
- score 抖动不让已发布推荐消失（active 成员资格 = 状态驱动，非分数驱动；§二十七 published_* 冻结）
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from src.clock import TestClock as _FakeClock
from src.config import AppConfigBundle
from src.domain import ConfidenceState, Direction, KlineEvent, KlineInterval, State, SystemMode
from src.recommendations import RecommendationStatus
from src.runtime import MarketRadarRuntime
from src.scoring.signal_confirmation import SignalConfirmationBreakdown
from src.storage import InMemoryRepository


def _make_runtime() -> MarketRadarRuntime:
    rt = MarketRadarRuntime(
        AppConfigBundle(), clock=_FakeClock(1_700_000_000_000),
        repository=InMemoryRepository(),
    )
    rt.system_mode = SystemMode.LIVE
    return rt


def _kline5m(symbol: str, open_time: int, *, closed: bool = True) -> KlineEvent:
    return KlineEvent(
        symbol=symbol, interval=KlineInterval.M5, open_time=open_time,
        close_time=open_time + 300_000, event_time=open_time, receive_time=open_time,
        open=Decimal("100"), high=Decimal("106"), low=Decimal("99"),
        close=Decimal("105"), volume=Decimal("10"), quote_volume=Decimal("100"),
        trade_count=100, is_closed=closed,
    )


def _good_plan() -> dict:
    return {
        "status": "ACTIVE", "chase_status": "ok", "trade_plan_id": "tp-1",
        "frozen": True, "reference_entry_low": 100.0, "reference_entry_high": 110.0,
        "rr_tp1": 2.0, "tp1": 120.0, "tp2": 130.0, "tp3": 140.0,
        "invalidation_price": 95.0,
    }


def _good_sig_bd() -> SignalConfirmationBreakdown:
    return SignalConfirmationBreakdown(
        score=80.0, available=True, core_passed=3, core_total=3,
        supporting_passed=3, supporting_total=5, veto_passed=True,
    )


def _vetoed_sig_bd() -> SignalConfirmationBreakdown:
    return SignalConfirmationBreakdown(
        score=80.0, available=True, core_passed=3, core_total=3,
        supporting_passed=3, supporting_total=5, veto_passed=False,
    )


def _breakout(confirmed: bool) -> SimpleNamespace:
    # 强确认字段置 False → 发布测试稳定得到 STANDARD（不依赖强确认路径）
    return SimpleNamespace(to_dict=lambda c=confirmed: {
        "breakout_confirmed": c,
        "breakout_hold": False, "retest_confirmed": False,
        "second_impulse_confirmed": False,
    })


def _fv() -> dict:
    return {"context_15m": 0.01, "context_1h": 0.005, "spot_perp_agreement": 0.4}


def _seed_good(rt: MarketRadarRuntime, symbol: str, *,
               setup: str = "ACCUMULATION",
               state: State = State.START_CONFIRMED) -> None:
    st = rt.get_state(symbol)
    st.state = state
    st.direction = Direction.LONG
    st.confidence_state = ConfidenceState.CONFIDENT
    st.opportunity_score = 80.0
    st.score_available = True
    st.signal_confirmation = 80.0
    st.signal_confirmation_available = True
    st.data_confidence = 90.0
    st.data_confidence_available = True
    st.pump_risk = 20.0
    st.stale_flag = 0.0
    st.setup_type = setup
    st.trade_plan = _good_plan()


def _gate_call(rt: MarketRadarRuntime, symbol: str, *,
               breakout_confirmed: bool = True) -> None:
    """便捷调用：注入当前时刻已收盘 5m bar + 良好输入 → _gate_and_publish。"""
    now = rt.clock.now_ms()
    rt.feature_engine.add_kline(_kline5m(symbol, now))
    rt._gate_and_publish(symbol, now, 105.0, _fv(), _good_sig_bd(),
                         _breakout(breakout_confirmed))


class Test5mDecisionBoundary:
    def test_no_5m_bar_blocks_publish(self):
        """§四：无 5m bar（未收盘决策窗口）→ 不得发布。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        rt._gate_and_publish("BTCUSDT", rt.clock.now_ms(), 105.0, _fv(),
                             _good_sig_bd(), _breakout(True))
        assert rt.published_repo.active() == []
        assert rt.get_state("BTCUSDT").gate_result is None  # 未评估

    def test_unclosed_5m_blocks_publish(self):
        """§四十二：5m 未收盘不得正式发布（runtime 边界）。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        now = rt.clock.now_ms()
        rt.feature_engine.add_kline(_kline5m("BTCUSDT", now, closed=False))
        rt._gate_and_publish("BTCUSDT", now, 105.0, _fv(), _good_sig_bd(),
                             _breakout(True))
        assert rt.published_repo.active() == []
        assert rt.get_state("BTCUSDT").gate_result is None

    def test_closed_5m_publishes(self):
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        recs = rt.published_repo.active()
        assert len(recs) == 1
        rec = recs[0]
        assert rec.symbol == "BTCUSDT"
        assert rec.status == RecommendationStatus.PUBLISHED
        assert rec.side == "LONG"
        assert rec.setup_type == "ACCUMULATION"
        assert rec.published_price == 105.0
        assert rec.current_price == 105.0
        assert rec.published_opportunity_score == 80.0
        assert rec.confirmation_level == "STANDARD"
        st = rt.get_state("BTCUSDT")
        assert st.recommendation_id == rec.recommendation_id
        assert st.gate_result is not None
        assert st.gate_result["passed"] is True

    def test_same_5m_bar_evaluated_once(self):
        """§四：同一 5m 收盘窗口只评估一次（_last_gate_bar 去重）。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        bar_open = rt._last_gate_bar["BTCUSDT"]
        # 再次「注入同一收盘 bar」→ 边界去重，不重复评估
        rt._gate_and_publish("BTCUSDT", rt.clock.now_ms(), 105.0, _fv(),
                             _good_sig_bd(), _breakout(True))
        assert rt._last_gate_bar["BTCUSDT"] == bar_open
        assert len(rt.published_repo.active()) == 1

    def test_breakout_setup_unclosed_breakout_not_published(self):
        """§四十二：突破类 Setup 未 5m 收盘确认突破 → 不发布。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT", setup="TREND_CONTINUATION")
        now = rt.clock.now_ms()
        rt.feature_engine.add_kline(_kline5m("BTCUSDT", now))
        rt._gate_and_publish("BTCUSDT", now, 105.0, _fv(), _good_sig_bd(),
                             _breakout(confirmed=False))
        assert rt.published_repo.active() == []
        st = rt.get_state("BTCUSDT")
        assert st.gate_result is not None
        assert st.gate_result["passed"] is False
        assert any("突破类 Setup" in f for f in st.gate_result["failed_checks"])

    def test_breakout_setup_confirmed_publishes(self):
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT", setup="TREND_CONTINUATION")
        _gate_call(rt, "BTCUSDT", breakout_confirmed=True)
        assert len(rt.published_repo.active()) == 1

    def test_not_live_blocks_publish(self):
        """§四/§四十一：非 LIVE 期不允许正式推荐（恢复期不发旧通知）。"""
        rt = _make_runtime()
        rt.system_mode = SystemMode.WARMUP
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        assert rt.published_repo.active() == []


class TestPublishDedupAndCooldown:
    def test_same_symbol_not_republished_while_active(self):
        """§四十二：同币短时间不得重复发布（活跃期间，新 5m 窗口也被去重）。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")          # bar A → 发布
        first_id = rt.published_repo.active()[0].recommendation_id
        rt.clock.advance(300_000)          # 推进到新 5m 收盘窗口 B（仍活跃）
        _gate_call(rt, "BTCUSDT")          # Gate 通过但 active_by_symbol 去重
        recs = rt.published_repo.active()
        assert len(recs) == 1
        assert recs[0].recommendation_id == first_id

    def test_cooldown_blocks_republish_within_window(self):
        """§三十四：同 symbol+方向+Setup 30m 内（终态后）冷却。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        rec_a = rt.published_repo.active()[0]
        rec_a.status = RecommendationStatus.EXITED
        rec_a.closed_at = rt.clock.now_ms()
        rt.published_repo.save(rec_a)
        assert rt.published_repo.active() == []   # 终态移出活跃区
        rt.clock.advance(600_000)                 # +10m（< 30m 冷却）→ 新窗口
        _gate_call(rt, "BTCUSDT")
        assert rt.published_repo.active() == []   # 冷却挡住

    def test_cooldown_expired_allows_republish(self):
        """§三十四：冷却过期后允许重新发布（新 recommendation_id）。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        rec_a = rt.published_repo.active()[0]
        rec_a.status = RecommendationStatus.EXITED
        rec_a.closed_at = rt.clock.now_ms()
        rt.published_repo.save(rec_a)
        rt.clock.advance(1_860_000)               # +31m（> 30m 冷却）
        _gate_call(rt, "BTCUSDT")
        recs = rt.published_repo.active()
        assert len(recs) == 1
        assert recs[0].recommendation_id != rec_a.recommendation_id

    def test_different_setup_bypasses_cooldown(self):
        """§三十四：换 Setup 不受旧方向+Setup 冷却限制。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT", setup="ACCUMULATION")
        _gate_call(rt, "BTCUSDT")
        rec_a = rt.published_repo.active()[0]
        rec_a.status = RecommendationStatus.EXITED
        rec_a.closed_at = rt.clock.now_ms()
        rt.published_repo.save(rec_a)
        rt.clock.advance(60_000)                  # +1m（冷却期内）
        _seed_good(rt, "BTCUSDT", setup="TREND_CONTINUATION")  # 换 Setup
        _gate_call(rt, "BTCUSDT", breakout_confirmed=True)
        assert len(rt.published_repo.active()) == 1

    def test_different_direction_bypasses_cooldown(self):
        """§三十四：换方向不受同 Setup 冷却限制。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")                  # LONG
        _gate_call(rt, "BTCUSDT")
        rec_a = rt.published_repo.active()[0]
        rec_a.status = RecommendationStatus.EXITED
        rec_a.closed_at = rt.clock.now_ms()
        rt.published_repo.save(rec_a)
        rt.clock.advance(60_000)
        st = rt.get_state("BTCUSDT")               # 换 SHORT
        st.direction = Direction.SHORT
        _gate_call(rt, "BTCUSDT")
        assert len(rt.published_repo.active()) == 1


class TestRecommendationRepositorySemantics:
    def test_empty_when_no_recommendations(self):
        """§四十二：无机会时首页允许 0 条（正式推荐仓库为空）。"""
        rt = _make_runtime()
        assert rt.published_repo.active() == []
        assert rt.published_repo.list_recent() == []

    def test_score_jitter_does_not_drop_active(self):
        """§四十二：实时 score 抖动不能让已发布 recommendation 消失。

        active 成员资格由 RecommendationStatus 驱动，而非分数；published_*
        冻结不可变（§二十七），current_* 可变但不影响成员资格（§三十三）。
        """
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        rec = rt.published_repo.active()[0]
        published_opp = rec.published_opportunity_score
        # 模拟 score 抖动：current 分大幅下降，状态转 WEAKENING（仍监督）
        rec.current_opportunity_score = 60.0
        rec.status = RecommendationStatus.WEAKENING
        rt.published_repo.save(rec)
        active = rt.published_repo.active()
        assert len(active) == 1
        assert active[0].recommendation_id == rec.recommendation_id
        assert active[0].published_opportunity_score == published_opp   # 冻结
        assert active[0].current_opportunity_score == 60.0              # 可变

    def test_terminal_rec_not_in_active(self):
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        rec = rt.published_repo.active()[0]
        rec.status = RecommendationStatus.INVALIDATED
        rt.published_repo.save(rec)
        assert rt.published_repo.active() == []
        assert rt.published_repo.all() == [rec]   # 终态保留可查


class TestSupervisePublishedWiring:
    """§六：验证 runtime._supervise_published 真正驱动已发布推荐生命周期。"""

    def test_normal_supervise_goes_monitoring(self):
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        now = rt.clock.now_ms()
        rt._supervise_published_slow("BTCUSDT", now, 106.0, _good_sig_bd())
        rec = rt.published_repo.active()[0]
        assert rec.status == RecommendationStatus.MONITORING
        assert rec.current_price == 106.0          # current_* 持续更新
        assert rec.published_price == 105.0        # published_* 冻结

    def test_withdrawal_driven_exits(self):
        """§四十二：Withdrawal 即时退出（经 runtime 编排路径）。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        rec_id = rt.published_repo.active()[0].recommendation_id
        rt.get_state("BTCUSDT").state = State.WITHDRAWAL
        now = rt.clock.now_ms()
        rt._supervise_published("BTCUSDT", now, 105.0, _good_sig_bd())
        rec = rt.published_repo.get(rec_id)
        assert rec is not None
        assert rec.status == RecommendationStatus.EXITED
        assert rec.exit_reason == "SIGNAL_WITHDRAWAL"
        assert rt.published_repo.active() == []
        assert rt.get_state("BTCUSDT").recommendation_id is None

    def test_hard_veto_driven_exits(self):
        """§四十二：Hard Veto 即时退出（经 runtime 编排路径）。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")
        rec_id = rt.published_repo.active()[0].recommendation_id
        now = rt.clock.now_ms()
        rt._supervise_published("BTCUSDT", now, 105.0, _vetoed_sig_bd())
        rec = rt.published_repo.get(rec_id)
        assert rec is not None
        assert rec.status == RecommendationStatus.INVALIDATED
        assert rec.exit_reason == "HARD_VETO"

    def test_price_invalidation_driven_exits(self):
        """§四.4：价格触及失效位 → 即时 Invalidation（LONG 跌破 invalidation）。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")          # side=LONG, invalidation=95.0
        rec_id = rt.published_repo.active()[0].recommendation_id
        now = rt.clock.now_ms()
        rt._supervise_published("BTCUSDT", now, 94.0, _good_sig_bd())  # 跌破 95
        rec = rt.published_repo.get(rec_id)
        assert rec is not None
        assert rec.status == RecommendationStatus.INVALIDATED
        assert rec.exit_reason == "INVALIDATION_HIT"

    def test_score_jitter_keeps_rec_active(self):
        """§三十三：score 抖动 → WEAKENING，不删除（经 runtime 编排路径）。"""
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        _gate_call(rt, "BTCUSDT")          # published opp=80
        st = rt.get_state("BTCUSDT")
        st.opportunity_score = 65.0        # < 70 门禁门槛
        now = rt.clock.now_ms()
        rt._supervise_published_slow("BTCUSDT", now, 105.0, _good_sig_bd())
        rec = rt.published_repo.active()[0]
        assert rec.status == RecommendationStatus.WEAKENING
        assert rec.is_active() is True
        assert rec.current_opportunity_score == 65.0
        assert rec.published_opportunity_score == 80.0   # 冻结

    def test_no_active_rec_is_noop(self):
        rt = _make_runtime()
        _seed_good(rt, "BTCUSDT")
        now = rt.clock.now_ms()
        rt._supervise_published("BTCUSDT", now, 105.0, _good_sig_bd())
        assert rt.published_repo.active() == []   # 未发布 → no-op
