"""UI 冒烟（§66.8 / §67）辅助服务器 — 注入种子数据的 runtime。

启动独立端口（默认 8051），复用 src.main 的全部路由与静态资源，
但绕过 lifespan（--lifespan off），把预置的种子 runtime 挂到
src.main.runtime 全局，使 /api/** 返回稳定的可视化数据，
用于 Playwright UI 冒烟：首页卡片 / 监督台 Kanban / 模拟验证 5 标签 / Drawer。

注意：这是测试夹具，不是产品代码。用法：
  .venv\\Scripts\\python.exe scripts\\ui_seed_server.py [port]
"""

from __future__ import annotations

import sys
from pathlib import Path

# 脚本以文件方式运行时，把仓库根目录加入 sys.path（否则 src 不可导入）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from src import main as app_module
from src.clock import SystemClock
from src.config import AppConfigBundle
from src.domain import AnalysisEvent, ConfidenceState, Direction, State, SystemMode
from src.market.regime import RegimeResult
from src.runtime import MarketRadarRuntime
from src.simulation.enums import SimulationStatus
from src.storage import InMemoryRepository

NOW = 1_760_000_000_000  # 固定 now，保证 ages/ago() 稳定

# 通用 features（§17 F 资金摘要所需键 + 翻译层键）
BASE_FEATURES = {
    "relative_volume": 1.87,
    "taker_bs": 1.32,
    "signed_delta": 184.5,
    "cvd": 412.0,
    "spot_cvd": 96.0,
    "oi_change_pct_5m": 2.3,
    "oi_change_pct_15m": 3.1,
    "oi_change_pct_1h": 6.1,
    "funding": 0.00012,
    "premium": 0.00021,
    "taker_buy_volume": 1520345.0,
    "taker_sell_volume": 1152000.0,
    "volume_z": 2.1,
    "trade_count": 320,
}

SCORE_LABELS = {
    "capital_inflow": "资金输入",
    "startup_quality": "启动质量",
    "trend": "趋势",
    "immediate_stamina": "即时续航",
    "sustained_startup": "持续启动",
    "chase_safety": "追涨安全",
    "top_risk": "顶部风险",
    "crowding_risk": "拥挤风险",
    "withdrawal_risk": "撤离风险",
    "chase_risk": "追涨风险",
}


def subscore(key: str, score: float, is_risk: bool = False) -> dict:
    return {
        "label": SCORE_LABELS.get(key, key),
        "score": round(score, 1),
        "available": True,
        "is_risk": is_risk,
        "coverage": 1.0,
        "missing": [],
        "components": [],
    }


def score_breakdown(opp: float) -> dict:
    return {
        "opportunity_score": opp,
        "available": True,
        "base_score": opp,
        "risk_penalty": 0.0,
        "coverage": 1.0,
        "missing": [],
        "subscores": {
            "capital_inflow": subscore("capital_inflow", 91),
            "startup_quality": subscore("startup_quality", 84),
            "trend": subscore("trend", 72),
            "immediate_stamina": subscore("immediate_stamina", 88),
            "sustained_startup": subscore("sustained_startup", 78),
            "chase_safety": subscore("chase_safety", 61),
            "top_risk": subscore("top_risk", 30, True),
            "crowding_risk": subscore("crowding_risk", 22, True),
            "withdrawal_risk": subscore("withdrawal_risk", 12, True),
            "chase_risk": subscore("chase_risk", 25, True),
        },
    }


def sig_conf_breakdown() -> dict:
    return {
        "score": 91.0, "score_pct": 91.0, "available": True,
        "core_passed": 4, "core_total": 4,
        "supporting_passed": 3, "supporting_total": 4,
        "veto_passed": True, "multi_tf_aligned": 2, "multi_tf_total": 3,
        "strong_confirm": True, "factors": {},
    }


def seed_symbol(
    rt: MarketRadarRuntime,
    symbol: str,
    state: State,
    *,
    opp: float,
    sig: float = 88.0,
    dc: float = 95.0,
    direction: Direction = Direction.LONG,
    setup: str = "ACCUMULATION",
    setup_label: str = "吸筹迹象",
    summary: str = "新增多头资金仍在进入，现货与合约方向一致。",
    pump: float | None = None,
    dist: float | None = None,
    accum: float | None = None,
    price_change: float = 8.5,
    trade_plan: dict | None = None,
    labels: list[str] | None = None,
    breakout: dict | None = None,
    structure: dict | None = None,
) -> None:
    st = rt.get_state(symbol)
    st.state = state
    st.direction = direction
    st.confidence_state = ConfidenceState.CONFIDENT
    st.features = dict(BASE_FEATURES)
    st.opportunity_score = opp
    st.score_available = True
    st.score_breakdown = score_breakdown(opp)
    st.data_confidence = dc
    st.data_confidence_available = True
    st.data_confidence_breakdown = {"score": dc, "available": True, "factors": {}}
    st.signal_confirmation = sig
    st.signal_confirmation_available = True
    st.signal_confirmation_breakdown = sig_conf_breakdown()
    st.summary = summary
    st.stale_flag = 0.0
    st.pump_risk = pump
    st.distribution_risk = dist
    st.accumulation_score = accum
    st.revival_score = 40.0
    st.impulse_label = "多空推动：买强卖弱"
    st.spot_perp_label = "现货 × 合约：同向"
    st.location_label = "突破后回踩区"
    st.trend_score = 72.0
    st.trend_label = "上升中"
    st.setup_type = setup
    st.setup_label = setup_label
    st.trade_plan = trade_plan or {}
    st.breakout_state = breakout or {
        "breakout_level": 0.0912, "breakout_time": NOW - 1800_000,
        "breakout_direction": "up", "breakout_confirmed": True, "breakout_hold": True,
        "time_above_level_ms": 1_900_000, "max_retrace": 0.012,
        "close_back_inside": False, "retest_started": True, "retest_depth": 0.006,
        "retest_confirmed": True, "strong_confirm": True,
        "confirmation_strength": "strong", "label": "二次确认",
    }
    st.structure_state = structure or {
        "local_high": 0.0931, "local_low": 0.0889, "resistance": 0.0935,
        "support": 0.0890, "swing_highs": [], "swing_lows": [], "structure_sequence": "HH-HL",
        "breakout_level": 0.0912, "retest_zone": [0.0905, 0.0915], "vwap": 0.0904, "atr": 0.0012,
    }
    st.spot_perp_state = {
        "spot_confirmed": True, "leverage_dominant": True, "classification": "spot_perp_aligned",
        "label": "现货 × 合约：同向", "spot_perp_agreement": True, "factors": {},
    }
    st.state_since_ms = NOW - 3_200_000
    st.last_update_ms = NOW
    st.evidence_count = 4
    st.veto_count = 0
    st.price_change_24h = price_change
    st.quote_volume_24h = 4.2e7
    rt.supervisor.update(
        symbol, state, setup_type=setup,
        labels=labels or [], now_ms=NOW,
    )
    # §42 状态时间线：为监督 Drawer 注入一条进入当前状态的过渡记录
    # （真实路径由 _process_symbol 写入 transition_history；种子夹具直接补）
    rt.transition_history.append(AnalysisEvent(
        symbol=symbol, direction=direction, previous_state=State.SLEEPING,
        new_state=state, asof=NOW - 3_200_000,
        confidence_state=ConfidenceState.CONFIDENT,
    ))
    rt._build_home_decision(symbol, NOW)


def seed_simulation(
    rt: MarketRadarRuntime,
    symbol: str,
    *,
    status: str,
    opp: float = 82.0,
    sig: float = 88.0,
    dc: float = 92.0,
    entry_price: float | None = None,
    current_price: float | None = None,
    pnl_pct: float = 0.0,
    mfe_pct: float = 0.0,
    mae_pct: float = 0.0,
    exit_reason: str | None = None,
    exit_price: float | None = None,
    result: dict | None = None,
) -> str:
    now = NOW
    plan = {
        "trade_plan_id": f"tp-{symbol}", "status": "ACTIVE", "frozen": True,
        "created_at": now, "version": 1,
        "reference_entry_low": 0.0905, "reference_entry_high": 0.0915,
        "tp1": 0.0940, "tp2": 0.0955, "tp3": 0.0970,
        "invalidation_price": 0.0885, "rr_tp1": 2.0, "rr_tp2": 3.2, "rr_tp3": 4.5,
        "chase_status": "ok", "plan_reason": "等待 0.0905~0.0915 回踩重新确认",
    }
    st = rt.get_state(symbol)
    st.trade_plan = plan
    if st.state == State.SLEEPING:
        st.state = State.START_CONFIRMED
        st.direction = Direction.LONG
    snap = rt.recommendation_snapshot_service.build(
        symbol=symbol, timestamp=now,
        market_regime=rt.market_regime.to_dict() if rt.market_regime else {},
        state=State.START_CONFIRMED, setup_type=st.setup_type,
        direction="LONG", current_price=entry_price or 0.0918,
        opportunity_score=opp, signal_confirmation=sig, data_confidence=dc,
        all_subscores={}, all_evidence=[], all_vetoes=[],
        breakout_state=st.breakout_state, structure_state=st.structure_state,
        spot_perp_state=st.spot_perp_state, trade_plan=plan,
    )
    rt.repository.save_recommendation_snapshot(symbol, now, snap.to_dict())
    item = rt.simulation_queue.create_from_snapshot(snap.to_dict(), now)
    sim_id = item.simulation_id

    if status in ("OPEN", "CLOSED"):
        item.entry_zone_reached_at = now
        item.entry_zone_reached_price = current_price or 0.0910
        item.armed_at = now
        item.entered_at = now
        item.entry_price = entry_price or 0.0912
        item.entry_reason = "Entry Zone 内第一笔符合 Revalidation 的价格（§28）"
        item.entry_confirmation = {"passed": True, "passed_checks": 8, "checks": 8}
        rt.simulation_positions.open(item, now + 1)
        pos = rt.simulation_positions.get(sim_id)
    if status == "OPEN":
        item.status = SimulationStatus.OPEN
        pos.current_price = current_price or 0.0918
        pos.current_pnl_pct = pnl_pct
        pos.mfe_pct = mfe_pct
        pos.mae_pct = mae_pct
    elif status == "CLOSED":
        item.status = SimulationStatus.CLOSED
        pos.current_price = exit_price or 0.0950
        pos.current_pnl_pct = pnl_pct
        pos.mfe_pct = mfe_pct
        pos.mae_pct = mae_pct
        pos.status = "CLOSED"
        pos.exit_reason = exit_reason
        pos.exit_price = exit_price or 0.0950
        pos.exit_time = now + 7_200_000
        pos.exit_is_dynamic = False
        pos.tp1_hit = True
        pos.static_tracking = False
        res = result or {
            "simulation_id": sim_id, "snapshot_id": snap.snapshot_id, "symbol": symbol,
            "direction": "LONG",
            "entry_time": now, "entry_price": entry_price or 0.0912, "entry_reason": "Entry Zone 内第一笔",
            "entry_confirmation": {"passed": True},
            "exit_time": now + 7_200_000, "exit_price": exit_price or 0.0950,
            "exit_reason": exit_reason or "TP1_HIT",
            "pnl_pct": pnl_pct, "mfe_pct": mfe_pct, "mae_pct": mae_pct,
            "tp1_hit": True, "tp2_hit": False, "tp3_hit": False, "invalidation_hit": False,
            "dynamic_exit_price": None,
            "static_plan_result": {
                "outcome": "TP1_HIT", "static_exit_price": 0.0950,
                "static_pnl_pct": 4.2, "tp1_hit": True, "tp2_hit": False, "tp3_hit": False,
                "stop_hit": False, "duration_hours": 2.0, "tracked_until_ms": None,
            },
            "duration_hours": 2.0, "closed_at": now + 7_200_000,
        }
        rt.repository.save_simulation_result(res)
        pos.result_persisted = True

    rt.repository.save_simulation_queue_item(item.to_dict())
    pos_after = rt.simulation_positions.get(sim_id)
    if pos_after is not None:
        rt.repository.save_simulation_position(pos_after.to_dict())
    return sim_id


def build_runtime() -> MarketRadarRuntime:
    rt = MarketRadarRuntime(AppConfigBundle(), clock=SystemClock(), repository=InMemoryRepository())
    rt.system_mode = SystemMode.LIVE
    rt.market_regime = RegimeResult("NEUTRAL", "中性", "BTC稳定 · 山寨偏弱 · 流动性一般")

    # 首页正式机会 + 模拟 OPEN
    seed_symbol(
        rt, "ONGUSDT", State.START_CONFIRMED,
        opp=86.2, sig=91.0, dc=96.0, setup="ACCUMULATION", setup_label="吸筹迹象",
        summary="新增多头资金仍在进入，现货与合约方向一致，当前尚未发现明显撤离。",
        price_change=27.6, accum=76.0, pump=18.0, dist=12.0)
    seed_simulation(rt, "ONGUSDT", status="OPEN", opp=86.2, sig=91.0, dc=96.0,
                    entry_price=0.0912, current_price=0.0918, pnl_pct=0.7, mfe_pct=1.6, mae_pct=-0.4)

    # 继续机会
    seed_symbol(rt, "ETHUSDT", State.CONTINUATION,
                opp=78.4, sig=85.0, dc=93.0, setup="TREND", setup_label="趋势跟随",
                summary="趋势资金持续，OI 保持扩张。", price_change=4.2,
                accum=58.0, pump=22.0, dist=8.0)

    # 重点观察（§14）
    seed_symbol(rt, "STXUSDT", State.SUSPECTED_START,
                opp=64.0, sig=71.0, dc=80.0, setup="RETEST_REIGNITION", setup_label="回踩复燃",
                summary="还缺：OI持续 + 5m收盘确认", price_change=12.0,
                labels=["retest_reignition"], accum=62.0, pump=30.0, dist=15.0)

    # 等待入场模拟（WATCHING）
    seed_symbol(rt, "BNBUSDT", State.START_CONFIRMED,
                opp=81.0, sig=87.0, dc=94.0, setup="ACCUMULATION", setup_label="吸筹迹象",
                summary="等待回踩参考关注区。", price_change=6.8,
                accum=70.0, pump=15.0, dist=10.0)
    seed_simulation(rt, "BNBUSDT", status="WATCHING", opp=81.0, sig=87.0, dc=94.0)

    # 风险 / 撤离
    seed_symbol(rt, "WIFUSDT", State.EXHAUSTION,
                opp=40.0, sig=60.0, dc=70.0, setup="DISTRIBUTION", setup_label="派发",
                summary="放量滞涨，可能出现高位派发。", price_change=-5.3,
                pump=85.0, dist=70.0, accum=None)
    seed_symbol(rt, "DOGEUSDT", State.WITHDRAWAL,
                opp=25.0, sig=45.0, dc=88.0, setup="DISTRIBUTION", setup_label="资金撤离",
                summary="OI + Delta 同步衰减，撤离确认。", price_change=-9.2,
                pump=40.0, dist=88.0, accum=None)
    # 异动（anomaly 池，不带 label → pool_for 归 anomaly）
    seed_symbol(rt, "SOLUSDT", State.ANOMALY,
                opp=55.0, sig=62.0, dc=75.0, setup="BREAKOUT_START", setup_label="突破启动",
                summary="放量突破，等待确认。", price_change=15.8,
                pump=48.0, dist=20.0, accum=55.0)

    # COOLDOWN 不应进入 Top Opportunity（§13 门槛外，§66.8 用例）
    seed_symbol(rt, "TRXUSDT", State.COOLDOWN,
                opp=97.0, sig=96.0, dc=95.0, setup="NONE", setup_label="冷却中",
                summary="冷却期，无正式推荐。", price_change=1.0, pump=5.0, dist=None, accum=None)

    # 已结束模拟（历史回放 + 统计）
    seed_symbol(rt, "AVAXUSDT", State.SLEEPING,
                opp=30.0, sig=50.0, dc=85.0, setup="ACCUMULATION", setup_label="吸筹迹象",
                summary="已完成一轮模拟验证。", price_change=1.2, pump=10.0, dist=None)
    seed_simulation(rt, "AVAXUSDT", status="CLOSED", opp=88.0, sig=93.0, dc=95.0,
                    entry_price=24.5, exit_price=25.8, pnl_pct=5.3, mfe_pct=6.1,
                    mae_pct=-1.2, exit_reason="TP1_HIT")

    # 数据健康页（§46 coverage + 明细表）需要 deep/universe 有 symbol
    all_syms = ["ONGUSDT", "ETHUSDT", "STXUSDT", "BNBUSDT", "WIFUSDT",
                "DOGEUSDT", "SOLUSDT", "TRXUSDT", "AVAXUSDT"]
    rt.universe.universe = list(all_syms)
    rt.deep_scanner.symbols = list(all_syms)

    rt._snapshot_plan_ids["ONGUSDT"] = {"tp-ONGUSDT"}
    rt._snapshot_plan_ids["BNBUSDT"] = {"tp-BNBUSDT"}
    rt._snapshot_plan_ids["AVAXUSDT"] = {"tp-AVAXUSDT"}
    return rt


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8051
    rt = build_runtime()
    app_module.runtime = rt
    print(f"[ui_seed_server] seeded runtime on port {port} (LIVE, universe seeded)")
    uvicorn.run("src.main:app", host="127.0.0.1", port=port, lifespan="off", log_level="warning")


if __name__ == "__main__":
    main()