"""V1 改造新增测试 — 依据改造任务文档 §27。

Feature: price_efficiency / retrace / RVOL / OI velocity
Detector: true short startup / short squeeze / long liquidation / one_bar_spike /
          delta_reversal veto / absorption veto
Integration: Evidence 不被 dashboard refresh 擦除（P0.2）
"""

from __future__ import annotations

from decimal import Decimal

from src.domain import (
    AggressorSide,
    ConfidenceState,
    Direction,
    EvidenceFamily,
    FeatureSnapshot,
    FeatureValue,
    VetoType,
)
from src.detectors.anomaly import AnomalyResult
from src.detectors.startup import StartupDetector
from src.detectors.false_start import FalseStartFilter
from src.features.efficiency_features import (
    compute_price_efficiency,
    compute_retrace_ratio,
)
from src.features.price_features import compute_price_return
from src.features.volume_features import compute_volume_features


def _trade(tid, price, qty, side, rt=None):
    rt = rt if rt is not None else tid * 1000
    return type("T", (), {
        "symbol": "BTCUSDT", "trade_id": tid, "event_time": rt, "receive_time": rt,
        "price": Decimal(str(price)), "qty": Decimal(str(qty)),
        "quote_notional": Decimal(str(price)) * Decimal(str(qty)),
        "aggressor_side": side, "is_maker": side == AggressorSide.SELL,
    })()


def _snap(**feats) -> FeatureSnapshot:
    features = {}
    for k, v in feats.items():
        features[k] = FeatureValue(value=v, available=v is not None, window="30s")
    return FeatureSnapshot(symbol="BTCUSDT", asof=1000, features=features)


# ── Feature 测试 ──


class TestPriceEfficiency:
    def test_positive_efficiency(self):
        """主动买推动价格上涨 → price_efficiency > 0。"""
        trades = [
            _trade(1, "100", "1", AggressorSide.BUY, 1000),
            _trade(2, "110", "1", AggressorSide.BUY, 2000),
        ]
        pe = compute_price_efficiency(trades, baseline_notional=100.0)
        assert pe is not None and pe > 0

    def test_absorption_low_efficiency(self):
        """巨大主动流量但价格不动 → price_effacity 极低/None。"""
        trades = [
            _trade(1, "100", "1000", AggressorSide.BUY, 1000),
            _trade(2, "100", "1000", AggressorSide.SELL, 2000),
        ]
        pe = compute_price_efficiency(trades, baseline_notional=100.0)
        # net_taker≈0 → norm 极小 → None
        assert pe is None

    def test_divide_by_zero_protection(self):
        """无方向性资金 → 不抛异常。"""
        trades = [_trade(1, "100", "1", AggressorSide.BUY, 1000)]
        assert compute_price_efficiency(trades) is None


class TestRetraceRatio:
    def test_full_retrace(self):
        """拉升后完整回吐 → retrace ≈ 1。"""
        trades = [
            _trade(1, "100", "1", AggressorSide.BUY, 1000),
            _trade(2, "110", "1", AggressorSide.BUY, 2000),
            _trade(3, "100", "1", AggressorSide.SELL, 3000),
        ]
        r = compute_retrace_ratio(trades)
        assert r is not None and r > 0.9


class TestPriceReturn:
    def test_multi_window(self):
        trades = [
            _trade(1, "100", "1", AggressorSide.BUY, 1000),
            _trade(2, "105", "1", AggressorSide.BUY, 2000),
        ]
        assert compute_price_return(trades) == 0.05


class TestRVOL:
    def test_rvol_with_baseline(self):
        trades = [_trade(1, "100", "0.5", AggressorSide.BUY)]
        r = compute_volume_features(trades, [0.1, 0.2, 0.15], [10, 20, 15])
        assert r.rvol is not None and abs(r.rvol - 3.333) < 0.01


# ── Detector 测试（§27）──


class TestStartupShort:
    def test_true_short_startup(self):
        """主动卖 + OI 扩张 + 效率 → SHORT SUSPECTED。"""
        snap = _snap(
            taker_delta=-10000, oi_change_1m=5.0,
            directional_efficiency=0.8, retrace_ratio=0.1, cvd_slope_z=-3.0,
        )
        det = StartupDetector()
        result = det.detect(snap, AnomalyResult(is_anomaly=True), confidence=ConfidenceState.CONFIDENT)
        assert result.suspected is True
        assert result.direction == Direction.SHORT
        assert result.subtype == "new_short_build"


class TestStartupSubtypes:
    def test_long_liquidation(self):
        """Price↓ OI↓ Delta↓ → long_liquidation。"""
        snap = _snap(
            taker_delta=-10000, oi_change_1m=-5.0,
            directional_efficiency=0.8, retrace_ratio=0.1,
        )
        det = StartupDetector()
        result = det.detect(snap, AnomalyResult(is_anomaly=True), confidence=ConfidenceState.CONFIDENT)
        # 方向 SHORT，OI 收缩 → long_liquidation（非新增空头，疑似多头被清算）
        assert result.direction == Direction.SHORT
        assert result.subtype == "long_liquidation"

    def test_short_squeeze_subtype(self):
        """Price↑ OI↓ Delta↑ → short_squeeze（非新增多头）。"""
        snap = _snap(
            taker_delta=10000, oi_change_1m=-5.0,
            directional_efficiency=0.8, retrace_ratio=0.1,
        )
        det = StartupDetector()
        result = det.detect(snap, AnomalyResult(is_anomaly=True), confidence=ConfidenceState.CONFIDENT)
        assert result.is_squeeze_cover is True
        assert result.subtype == "short_squeeze"
        assert result.suspected is False  # squeeze 不作为新增多头 suspected


class TestFalseStartVetoes:
    def test_one_bar_spike_rejected(self):
        """5s 剧烈位移 + 30s 大幅回吐 → one_bar_spike hard veto。"""
        snap = _snap(
            price_return_5s=0.10, price_return_30s=0.01, retrace_ratio=0.8,
        )
        det = FalseStartFilter()
        result = det.check(snap, direction=Direction.LONG)
        veto = next(v for v in result.vetoes if v.type == VetoType.ONE_BAR_SPIKE)
        assert veto.triggered is True
        assert result.rejected is True

    def test_delta_reversal_veto(self):
        """LONG 但 delta 反转 → delta_reversal hard veto。"""
        snap = _snap(taker_delta=-10000)
        det = FalseStartFilter()
        result = det.check(snap, direction=Direction.LONG)
        veto = next(v for v in result.vetoes if v.type == VetoType.DELTA_REVERSAL)
        assert veto.triggered is True
        assert result.rejected is True

    def test_absorption_soft_veto(self):
        """delta 大但 flow_impact 极低 → soft veto（不直接拒绝）。"""
        snap = _snap(taker_delta=50000, flow_impact=0.0001, retrace_ratio=0.1,
                     oi_change_1m=5.0, directional_efficiency=0.8)
        det = FalseStartFilter()
        result = det.check(snap, direction=Direction.LONG)
        veto = next(v for v in result.vetoes if v.type == VetoType.LOW_EFFICIENCY_ABSORPTION)
        assert veto.triggered is True
        assert veto.severity.value == "soft"

    def test_veto_has_explanatory_detail(self):
        """每个 veto 必须带 detail（值/阈值/原因）。"""
        snap = _snap(retrace_ratio=0.9)
        det = FalseStartFilter()
        result = det.check(snap, direction=Direction.LONG)
        for v in result.vetoes:
            assert v.detail is not None
            assert "reason" in v.detail


# ── Integration: Evidence 不被 refresh 擦除（P0.2）──


class TestEvidencePreservation:
    def test_evidence_preserved_through_refresh(self):
        """模拟：真实 transition 有 evidence → 多次 compute refresh → evidence 仍在。"""
        from src.config import AppConfigBundle
        from src.runtime import MarketRadarRuntime
        from src.clock import TestClock
        from src.domain import AnalysisEvent, State

        cfg = AppConfigBundle()
        rt = MarketRadarRuntime(cfg, clock=TestClock(0))
        # 手动注入一个真实 transition（含 evidence）
        ev = AnalysisEvent(
            symbol="BTCUSDT", direction=Direction.LONG,
            previous_state=State.ANOMALY, new_state=State.SUSPECTED_START,
            evidence=[__import__("src.domain", fromlist=["Evidence"]).Evidence(
                family=EvidenceFamily.FLOW, type="taker_delta", value=10000.0, passed=True)],
            vetoes=[],
            asof=1000, confidence_state=ConfidenceState.CONFIDENT,
        )
        rt.last_evidence_transition["BTCUSDT"] = ev
        rt.last_transition["BTCUSDT"] = ev
        rt.transition_history.append(ev)
        st = rt.get_state("BTCUSDT")
        st.evidence_count = 1

        # 模拟多次 refresh（不产生新 transition）
        for _ in range(5):
            # get_symbol_detail 应仍返回 evidence
            detail = rt.get_symbol_detail("BTCUSDT")
            assert len(detail["evidence"]) == 1
            radar = rt.get_radar()
            r = next(x for x in radar if x["symbol"] == "BTCUSDT")
            assert r["evidence_count"] == 1

    def test_automatic_cooldown_does_not_erase_evidence(self):
        """自动 REJECTED→COOLDOWN（无 evidence）不应擦除之前的 evidence。"""
        from src.config import AppConfigBundle
        from src.runtime import MarketRadarRuntime
        from src.clock import TestClock
        from src.domain import AnalysisEvent, State, Evidence

        cfg = AppConfigBundle()
        rt = MarketRadarRuntime(cfg, clock=TestClock(0))
        rt.get_state("ETHUSDT")  # 创建 latest_state
        # 含证据的 REJECTED transition
        rejected = AnalysisEvent(
            symbol="ETHUSDT", direction=Direction.LONG,
            previous_state=State.SUSPECTED_START, new_state=State.REJECTED,
            evidence=[], vetoes=[__import__("src.domain", fromlist=["Veto"]).Veto(
                type=VetoType.RAPID_RETRACE, triggered=True, severity=__import__("src.domain", fromlist=["VetoSeverity"]).VetoSeverity.HARD)],
            asof=2000, confidence_state=ConfidenceState.CONFIDENT,
        )
        rt.last_evidence_transition["ETHUSDT"] = rejected
        rt.last_transition["ETHUSDT"] = rejected
        # 自动 COOLDOWN transition（无 evidence/veto）
        cooldown = AnalysisEvent(
            symbol="ETHUSDT", direction=None,
            previous_state=State.REJECTED, new_state=State.COOLDOWN,
            evidence=[], vetoes=[], asof=3000, confidence_state=ConfidenceState.CONFIDENT,
        )
        rt.last_transition["ETHUSDT"] = cooldown
        rt.transition_history.extend([rejected, cooldown])

        # detail 应仍显示 REJECTED 的 veto（未被 COOLDOWN 擦除）
        detail = rt.get_symbol_detail("ETHUSDT")
        assert len(detail["vetoes"]) == 1
        assert detail["vetoes"][0]["type"] == "rapid_retrace"
