"""V1.3 P2 API 测试（§63–§65）：runtime 数据访问层 + 端点语义。

直接测 MarketRadarRuntime 的数据访问方法（与 test_dashboard.py 测
DashboardService 一致），不经过 src.main 的 lifespan（避免真实网络）。
"""

from __future__ import annotations

from src.clock import Clock
from src.config import AppConfigBundle
from src.domain import ConfidenceState, Direction, State, SystemMode
from src.runtime import MarketRadarRuntime
from src.storage import InMemoryRepository
from src.supervision.state_pool import PoolName


class TClock(Clock):
    def now_ms(self) -> int:
        return 1_700_000_000_000


def _make_runtime() -> MarketRadarRuntime:
    rt = MarketRadarRuntime(AppConfigBundle(), clock=TClock(), repository=InMemoryRepository())
    rt.system_mode = SystemMode.LIVE
    return rt


def _seed_symbol(rt: MarketRadarRuntime, symbol: str, state: State,
                 opp: float = 82.0, conf: float = 92.0, sig: float = 88.0,
                 labels: list[str] | None = None) -> None:
    st = rt.get_state(symbol)
    st.state = state
    st.direction = Direction.LONG
    st.confidence_state = ConfidenceState.CONFIDENT
    st.opportunity_score = opp
    st.score_available = True
    st.data_confidence = conf
    st.data_confidence_available = True
    st.signal_confirmation = sig
    st.signal_confirmation_available = True
    st.stale_flag = 0.0
    st.pump_risk = 20.0
    st.setup_type = "ACCUMULATION"
    st.summary = "资金流入"
    rt.supervisor.update(symbol, state, setup_type="ACCUMULATION",
                         labels=labels or [], now_ms=rt.clock.now_ms())


def _seed_simulation(rt: MarketRadarRuntime, symbol: str = "BTCUSDT") -> str:
    """冻结快照 → WATCHING 入队 → 模拟建仓。返回 simulation_id。"""
    now = rt.clock.now_ms()
    st = rt.get_state(symbol)
    st.trade_plan = {
        "trade_plan_id": "tp-1", "status": "ACTIVE", "frozen": True,
        "reference_entry_low": 61000.0, "reference_entry_high": 61500.0,
        "tp1": 62500.0, "tp2": 63000.0, "tp3": 63500.0, "invalidation_price": 60500.0,
    }
    snap = rt.recommendation_snapshot_service.build(
        symbol=symbol, timestamp=now,
        market_regime={"regime": "NEUTRAL", "label": "中性", "detail": "", "factors": {}},
        state=State.START_CONFIRMED, setup_type="ACCUMULATION",
        direction="LONG", current_price=61200.0,
        opportunity_score=82.0, signal_confirmation=88.0, data_confidence=92.0,
        all_subscores={}, all_evidence=[], all_vetoes=[],
        breakout_state={}, structure_state={}, spot_perp_state={}, trade_plan=st.trade_plan,
    )
    rt.repository.save_recommendation_snapshot(symbol, now, snap.to_dict())
    item = rt.simulation_queue.create_from_snapshot(snap.to_dict(), now)
    item.entered_at = now
    item.entry_price = 61200.0
    rt.repository.save_simulation_queue_item(item.to_dict())
    rt.simulation_positions.open(item, now + 1)
    return item.simulation_id


class TestHomeAPI:
    def test_structure(self):
        rt = _make_runtime()
        _seed_symbol(rt, "BTCUSDT", State.START_CONFIRMED)
        _seed_symbol(rt, "ETHUSDT", State.SUSPECTED_START, labels=["accumulation"])
        home = rt.get_home()
        assert set(home) == {"market_regime", "health", "published_recommendations",
                             "confirmed_opportunities", "watch_candidates", "risk_candidates"}
        assert home["health"] is not None  # 覆盖率 dict
        # §十.2：首页正式机会读取 PublishedRecommendationRepository（空时为 []，§九允许 0 条）
        assert home["published_recommendations"] == []
        # §13 正式门槛：START_CONFIRMED + 高分会进 confirmed
        syms = [r["symbol"] for r in home["confirmed_opportunities"]]
        assert "BTCUSDT" in syms
        # §14 正在观察：SUSPECTED_START 进 watch，不进 confirmed
        watch_syms = {r["symbol"] for r in home["watch_candidates"]}
        assert "ETHUSDT" in watch_syms
        assert "ETHUSDT" not in syms

    def test_confirmed_row_has_decision_snapshot(self):
        rt = _make_runtime()
        _seed_symbol(rt, "BTCUSDT", State.START_CONFIRMED)
        now = rt.clock.now_ms()
        rt._build_home_decision("BTCUSDT", now)
        home = rt.get_home()
        row = next(r for r in home["confirmed_opportunities"] if r["symbol"] == "BTCUSDT")
        assert row["decision_snapshot"].get("decision", {}).get("opportunity_score") == 82.0

    def test_watch_candidates_capped(self):
        rt = _make_runtime()
        for i in range(8):
            _seed_symbol(rt, f"SYM{i}", State.SUSPECTED_START, opp=60.0 + i)
        home = rt.get_home()
        assert len(home["watch_candidates"]) == rt.cfg.ranking.watch_max_items

    def test_non_live_home_confirmed_empty(self):
        rt = _make_runtime()
        rt.system_mode = SystemMode.WARMUP
        _seed_symbol(rt, "BTCUSDT", State.START_CONFIRMED)
        home = rt.get_home()
        assert home["confirmed_opportunities"] == []


class TestSupervisionAPI:
    def test_kanban_by_pool(self):
        rt = _make_runtime()
        _seed_symbol(rt, "BTCUSDT", State.START_CONFIRMED)
        _seed_symbol(rt, "ETHUSDT", State.SUSPECTED_START, labels=["accumulation"])
        _seed_symbol(rt, "SOLUSDT", State.START_CONFIRMED)
        kanban = rt.get_supervision_kanban()
        assert "confirmed" in kanban and "watch" in kanban
        confirmed_syms = {r["symbol"] for r in kanban["confirmed"]}
        assert {"BTCUSDT", "SOLUSDT"} <= confirmed_syms
        watch_syms = {r["symbol"] for r in kanban["watch"]}
        assert "ETHUSDT" in watch_syms

    def test_symbol_detail_and_404(self):
        rt = _make_runtime()
        _seed_symbol(rt, "BTCUSDT", State.START_CONFIRMED)
        detail = rt.get_supervision_symbol("BTCUSDT")
        assert detail is not None
        assert detail["current_pool"] == PoolName.CONFIRMED.value
        assert "supervision_question" in detail
        assert rt.get_supervision_symbol("NONEXIST") is None


class TestSimulationAPI:
    def test_simulations_list(self):
        rt = _make_runtime()
        sim_id = _seed_simulation(rt)
        data = rt.get_simulations()
        assert [i["simulation_id"] for i in data["queue"]] == [sim_id]
        assert data["open_positions"], "建仓后应有 OPEN 持仓"
        assert data["positions"][0]["status"] == "OPEN"
        assert data["results"] == []

    def test_simulation_detail(self):
        rt = _make_runtime()
        sim_id = _seed_simulation(rt)
        detail = rt.get_simulation(sim_id)
        assert detail is not None
        assert detail["item"]["simulation_id"] == sim_id
        assert detail["position"]["symbol"] == "BTCUSDT"
        assert detail["events"] == []
        assert rt.get_simulation("NONEXIST") is None

    def test_statistics(self):
        rt = _make_runtime()
        _seed_simulation(rt)
        stats = rt.get_simulation_statistics()
        assert stats["overview"]["recommendations"] == 1
        assert stats["overview"]["entries"] == 1
        assert set(stats) == {"overview", "buckets", "setup_conversion"}