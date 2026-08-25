"""V1.3 §46 数据健康覆盖率 — compute_coverage + RuntimeEngine.get_health_coverage。"""

from __future__ import annotations

from src.clock import TestClock
from src.config import AppConfigBundle
from src.domain import HealthLevel
from src.health.coverage import (
    LEVEL_ANOMALY,
    LEVEL_CRITICAL,
    LEVEL_DEGRADED,
    LEVEL_NORMAL,
    CoverageResult,
    compute_coverage,
)
from src.health.freshness_watchdog import StreamType
from src.runtime import MarketRadarRuntime
from src.storage import InMemoryRepository


def _pairs(ok: int, total: int, prefix: str = "aggTrade") -> list[tuple[str, HealthLevel]]:
    """构造 ok 个健康 + (total-ok) 个 FAIL 的配对（同前缀不同 symbol）。"""
    return [
        (f"{prefix}:SYM{i}", HealthLevel.OK if i < ok else HealthLevel.FAIL)
        for i in range(total)
    ]


class TestCoverageLevels:
    def test_normal_95(self):
        r = compute_coverage(_pairs(38, 40))
        assert r.coverage_pct == 95.0
        assert r.healthy_pairs == 38
        assert r.total_pairs == 40
        assert r.level == LEVEL_NORMAL
        assert r.level_label == "正常"
        assert r.critical_stream_down is False

    def test_ok_and_warn_both_healthy(self):
        pairs = [("aggTrade:S1", HealthLevel.OK), ("aggTrade:S2", HealthLevel.WARN)]
        r = compute_coverage(pairs)
        assert r.coverage_pct == 100.0
        assert r.level == LEVEL_NORMAL

    def test_degraded_80(self):
        r = compute_coverage(_pairs(32, 40))
        assert r.coverage_pct == 80.0
        assert r.level == LEVEL_DEGRADED
        assert r.level_label == "部分降级"

    def test_anomaly_60(self):
        r = compute_coverage(_pairs(24, 40))
        assert r.coverage_pct == 60.0
        assert r.level == LEVEL_ANOMALY
        assert r.level_label == "异常"

    def test_boundary_90_is_normal(self):
        r = compute_coverage(_pairs(36, 40))
        assert r.coverage_pct == 90.0
        assert r.level == LEVEL_NORMAL

    def test_boundary_70_is_degraded(self):
        r = compute_coverage(_pairs(28, 40))
        assert r.coverage_pct == 70.0
        assert r.level == LEVEL_DEGRADED

    def test_empty_pairs_vacuous_normal(self):
        r = compute_coverage([])
        assert r.coverage_pct == 100.0
        assert r.total_pairs == 0
        assert r.level == LEVEL_NORMAL
        assert r.critical_stream_down is False
        assert r.per_stream == {}

    def test_string_levels_equal_enum(self):
        pairs = [("aggTrade:S1", "OK"), ("aggTrade:S2", "FAIL")]
        r = compute_coverage(pairs)
        assert r.coverage_pct == 50.0
        assert r.level == LEVEL_ANOMALY


class TestCriticalCoreDown:
    """核心数据源（aggTrade）整体断线 → 严重异常，无论覆盖率。"""

    def test_all_core_fail_high_coverage(self):
        # 10 个 aggTrade 全 FAIL，其余 30 个流全 OK → 75% 覆盖率但严重异常
        pairs = []
        for i in range(10):
            pairs.append((f"aggTrade:S{i}", HealthLevel.FAIL))
        for i in range(10):
            for prefix in ("kline", "oi_poller", "funding_premium"):
                pairs.append((f"{prefix}:S{i}", HealthLevel.OK))
        r = compute_coverage(pairs)
        assert r.coverage_pct == 75.0
        assert r.level == LEVEL_CRITICAL
        assert r.level_label == "严重异常"
        assert r.critical_stream_down is True

    def test_all_core_stale_is_critical(self):
        # STALE 同样视为不健康 → 核心整体断线
        pairs = [(f"aggTrade:S{i}", HealthLevel.STALE) for i in range(5)]
        r = compute_coverage(pairs)
        assert r.critical_stream_down is True
        assert r.level == LEVEL_CRITICAL

    def test_one_core_ok_not_critical(self):
        # 核心流只要有一个健康 → 不是整体断线，按覆盖率分级
        pairs = [(f"aggTrade:S{i}", HealthLevel.FAIL) for i in range(10)]
        pairs.append(("aggTrade:S10", HealthLevel.OK))
        for i in range(11):
            for prefix in ("kline", "oi_poller", "funding_premium"):
                pairs.append((f"{prefix}:S{i}", HealthLevel.OK))
        r = compute_coverage(pairs)
        assert r.coverage_pct == 77.3  # 34/44 = 77.27 → round 77.3
        assert r.critical_stream_down is False
        assert r.level == LEVEL_DEGRADED

    def test_custom_critical_prefix(self):
        # 自定义核心流前缀：oi_poller 整体 FAIL → 严重异常
        pairs = []
        for i in range(8):
            pairs.append((f"oi_poller:S{i}", HealthLevel.FAIL))
        for i in range(8):
            pairs.append((f"aggTrade:S{i}", HealthLevel.OK))
        r = compute_coverage(pairs, critical_stream_prefix="oi_poller")
        assert r.critical_stream_down is True
        assert r.level == LEVEL_CRITICAL


class TestPerStreamBreakdown:
    def test_per_stream_pct(self):
        pairs = [
            ("aggTrade:S1", HealthLevel.OK),
            ("aggTrade:S2", HealthLevel.OK),
            ("aggTrade:S3", HealthLevel.OK),
            ("aggTrade:S4", HealthLevel.OK),
            ("aggTrade:S5", HealthLevel.FAIL),  # 4/5 → 80.0
            ("kline:S1", HealthLevel.OK),
            ("kline:S2", HealthLevel.OK),
            ("kline:S3", HealthLevel.FAIL),
            ("kline:S4", HealthLevel.FAIL),
            ("kline:S5", HealthLevel.FAIL),  # 2/5 → 40.0
        ]
        r = compute_coverage(pairs)
        assert r.per_stream == {"aggTrade": 80.0, "kline": 40.0}
        assert r.coverage_pct == 60.0  # 6/10

    def test_unnamed_pairs_excluded_from_per_stream(self):
        # 无名字的配对计入总覆盖率，但不出现 per_stream 分组
        pairs = [("", HealthLevel.OK), ("", HealthLevel.FAIL), ("aggTrade:S1", HealthLevel.OK)]
        r = compute_coverage(pairs)
        assert r.coverage_pct == round(200.0 / 3, 1)
        assert r.per_stream == {"aggTrade": 100.0}

    def test_to_dict_roundtrip(self):
        r = compute_coverage(_pairs(38, 40))
        d = r.to_dict()
        assert d["coverage_pct"] == 95.0
        assert d["healthy_pairs"] == 38
        assert d["level"] == LEVEL_NORMAL
        assert d["level_label"] == "正常"
        assert d["critical_stream_down"] is False
        assert isinstance(d["per_stream"], dict)


def _register_all_ok(rt: MarketRadarRuntime, symbols: list[str]) -> None:
    for sym in symbols:
        for prefix, stype in (
            ("aggTrade", StreamType.AGGTRADE),
            ("kline", StreamType.KLINE),
            ("oi_poller", StreamType.OI_POLLER),
            ("funding_premium", StreamType.FUNDING_PREMIUM),
        ):
            sid = f"{prefix}:{sym}"
            rt.watchdog.register_stream(sid, sym, stype)
            rt.watchdog.mark_connected(sid, True)
            rt.watchdog.record_event(sid, 0, 0)


class TestRuntimeCoverage:
    def _rt(self) -> MarketRadarRuntime:
        return MarketRadarRuntime(
            AppConfigBundle(), clock=TestClock(0), repository=InMemoryRepository()
        )

    def test_all_healthy_100(self):
        rt = self._rt()
        rt.deep_scanner.symbols = ["BTCUSDT", "ETHUSDT"]
        _register_all_ok(rt, ["BTCUSDT", "ETHUSDT"])
        d = rt.get_health_coverage()
        assert d["coverage_pct"] == 100.0
        assert d["level"] == LEVEL_NORMAL
        assert d["level_label"] == "正常"
        assert d["critical_stream_down"] is False
        assert d["per_stream"] == {
            "aggTrade": 100.0, "kline": 100.0, "oi_poller": 100.0, "funding_premium": 100.0,
        }

    def test_unregistered_streams_critical(self):
        # 无任何 stream 注册 → 全部 FAIL → 核心流整体断线 → 严重异常
        rt = self._rt()
        rt.deep_scanner.symbols = ["BTCUSDT"]
        d = rt.get_health_coverage()
        assert d["coverage_pct"] == 0.0
        assert d["level"] == LEVEL_CRITICAL
        assert d["critical_stream_down"] is True
        assert d["per_stream"] == {
            "aggTrade": 0.0, "kline": 0.0, "oi_poller": 0.0, "funding_premium": 0.0,
        }

    def test_one_oi_stale_degrades_coverage(self):
        # only one coin's OI 延迟 → 7/8=87.5% → 部分降级，不再整页"数据异常"
        rt = self._rt()
        symbols = ["BTCUSDT", "ETHUSDT"]
        rt.deep_scanner.symbols = symbols
        _register_all_ok(rt, symbols)
        # 推进时钟，然后只给除 oi_poller:ETHUSDT 外的所有流推新事件：
        # 目标流的 age=20s > oi_poller budget 10s → STALE；其余流仍是新数据。
        rt.clock.set(20_000)
        for sym in symbols:
            for prefix, stype in (
                ("aggTrade", StreamType.AGGTRADE),
                ("kline", StreamType.KLINE),
                ("oi_poller", StreamType.OI_POLLER),
                ("funding_premium", StreamType.FUNDING_PREMIUM),
            ):
                if (prefix, sym) == ("oi_poller", "ETHUSDT"):
                    continue
                rt.watchdog.record_event(f"{prefix}:{sym}")
        hs = rt.watchdog.check_health("oi_poller:ETHUSDT")
        assert hs.status == HealthLevel.STALE
        d = rt.get_health_coverage()
        assert d["coverage_pct"] == 87.5  # 7/8 → 70~90 → 部分降级
        assert d["level"] == LEVEL_DEGRADED
        assert d["level_label"] == "部分降级"
        assert d["critical_stream_down"] is False
        assert d["per_stream"]["oi_poller"] == 50.0
        assert d["per_stream"]["aggTrade"] == 100.0