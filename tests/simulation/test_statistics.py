"""§37–§39 Simulation Statistics — 总览 / 分桶 / Setup 转化率（纯函数）。"""

from __future__ import annotations

from src.simulation import SimulationStatistics


def _rec(snapshot_id="S1", **over):
    r = dict(
        snapshot_id=snapshot_id, symbol="BTCUSDT", direction="LONG",
        entry_time=0, entry_price=100.0, exit_time=1000, exit_price=110.0,
        exit_reason="TP1_HIT", pnl_pct=10.0, mfe_pct=12.0, mae_pct=-2.0,
        tp1_hit=True, tp2_hit=False, tp3_hit=False, invalidation_hit=False,
        duration_hours=1.0,
    )
    r.update(over)
    return r


def _snap(snapshot_id="S1", **over):
    s = dict(
        snapshot_id=snapshot_id, symbol="BTCUSDT", state="START_CONFIRMED",
        setup_type="TREND_CONTINUATION", direction="LONG",
        primary_timeframe="15m", opportunity_score=85.0,
        signal_confirmation=88.0, data_confidence=92.0,
        market_regime={"regime": "TREND_UP"},
    )
    s.update(over)
    return s


def _queue(snapshot_id="S1", **over):
    q = dict(
        simulation_id=snapshot_id, snapshot_id=snapshot_id, symbol="BTCUSDT",
        status="CLOSED", entered_at=500, entry_zone_reached_at=300,
        armed_at=400, revalidate_result={"passed": True},
    )
    q.update(over)
    return q


class TestOverview:
    def test_empty(self):
        st = SimulationStatistics.compute([], [], [])
        ov = st["overview"]
        assert ov["recommendations"] == 0
        assert ov["closed"] == 0
        assert ov["avg_mfe_pct"] == 0.0
        assert ov["avg_mae_pct"] == 0.0

    def test_counts(self):
        snaps = [_snap("S1"), _snap("S2", setup_type="RETEST_REIGNITION")]
        queues = [_queue("S1"), _queue("S2", entered_at=None, entry_zone_reached_at=100)]
        results = [_rec("S1")]
        ov = SimulationStatistics.compute(snaps, queues, results)["overview"]
        assert ov["recommendations"] == 2
        assert ov["zone_reached"] == 2
        assert ov["revalidation_passed"] == 2
        assert ov["entries"] == 1          # 只有 S1 entered_at
        assert ov["closed"] == 1
        assert ov["tp1_hit"] == 1

    def test_exit_reason_counts(self):
        snaps = [_snap("S1"), _snap("S2"), _snap("S3"), _snap("S4")]
        queues = [_queue("S1"), _queue("S2"), _queue("S3"), _queue("S4")]
        results = [
            _rec("S1", exit_reason="SIGNAL_WITHDRAWAL", pnl_pct=1.0, mfe_pct=3.0, mae_pct=-1.0),
            _rec("S2", exit_reason="DISTRIBUTION_EXIT", pnl_pct=2.0, mfe_pct=4.0, mae_pct=-2.0),
            _rec("S3", exit_reason="DIRECTION_FLIP", pnl_pct=3.0, mfe_pct=5.0, mae_pct=-3.0),
            _rec("S4", exit_reason="TIME_EXPIRED", pnl_pct=-1.0, mfe_pct=1.0, mae_pct=-4.0),
        ]
        ov = SimulationStatistics.compute(snaps, queues, results)["overview"]
        assert ov["withdrawal_exit"] == 1
        assert ov["distribution_exit"] == 1
        assert ov["direction_flip"] == 1
        assert ov["time_expired"] == 1
        assert ov["avg_mfe_pct"] == 3.25
        assert ov["avg_mae_pct"] == -2.5


class TestBuckets:
    def test_opportunity_buckets(self):
        snaps = [
            _snap("S1", opportunity_score=65.0),
            _snap("S2", opportunity_score=75.0),
            _snap("S3", opportunity_score=85.0),
            _snap("S4", opportunity_score=95.0),
        ]
        queues = [_queue(f"S{i}") for i in range(1, 5)]
        results = [_rec(f"S{i}", pnl_pct=1.0) for i in range(1, 5)]
        b = SimulationStatistics.compute(snaps, queues, results)["buckets"]["opportunity_score"]
        assert b["0-69"]["count"] == 1
        assert b["70-79"]["count"] == 1
        assert b["80-89"]["count"] == 1
        assert b[">=90"]["count"] == 1

    def test_signal_confirmation_buckets(self):
        snaps = [
            _snap("S1", signal_confirmation=70.0),
            _snap("S2", signal_confirmation=80.0),
            _snap("S3", signal_confirmation=90.0),
        ]
        queues = [_queue(f"S{i}") for i in range(1, 4)]
        results = [_rec(f"S{i}", pnl_pct=2.0) for i in range(1, 4)]
        b = SimulationStatistics.compute(snaps, queues, results)["buckets"]["signal_confirmation"]
        assert b["0-74"]["count"] == 1
        assert b["75-84"]["count"] == 1
        assert b[">=85"]["count"] == 1

    def test_setup_type_cumulative(self):
        snaps = [
            _snap("S1", setup_type="TREND_CONTINUATION"),
            _snap("S2", setup_type="TREND_CONTINUATION"),
            _snap("S3", setup_type="RETEST_REIGNITION"),
        ]
        queues = [_queue(f"S{i}") for i in range(1, 4)]
        results = [
            _rec("S1", pnl_pct=4.0), _rec("S2", pnl_pct=6.0), _rec("S3", pnl_pct=2.0),
        ]
        b = SimulationStatistics.compute(snaps, queues, results)["buckets"]["setup_type"]
        assert b["TREND_CONTINUATION"]["cum_pnl_pct"] == 10.0
        assert b["RETEST_REIGNITION"]["cum_pnl_pct"] == 2.0

    def test_direction_and_timeframe_buckets(self):
        snaps = [
            _snap("S1", direction="LONG", primary_timeframe="15m"),
            _snap("S2", direction="SHORT", primary_timeframe="1h"),
        ]
        queues = [_queue(f"S{i}") for i in range(1, 3)]
        results = [_rec(f"S{i}", pnl_pct=1.0) for i in range(1, 3)]
        b = SimulationStatistics.compute(snaps, queues, results)["buckets"]
        assert b["direction"]["LONG"]["cum_pnl_pct"] == 1.0
        assert b["direction"]["SHORT"]["cum_pnl_pct"] == 1.0
        assert b["timeframe"]["15m"]["cum_pnl_pct"] == 1.0
        assert b["timeframe"]["1h"]["cum_pnl_pct"] == 1.0

    def test_market_regime_from_snapshot(self):
        snaps = [
            _snap("S1", market_regime={"regime": "TREND_UP"}),
            _snap("S2", market_regime={"regime": "TREND_UP"}),
            _snap("S3", market_regime={"regime": "RANGE"}),
        ]
        queues = [_queue(f"S{i}") for i in range(1, 4)]
        results = [_rec(f"S{i}", pnl_pct=1.0) for i in range(1, 4)]
        b = SimulationStatistics.compute(snaps, queues, results)["buckets"]["market_regime"]
        assert b["TREND_UP"]["count"] == 2
        assert b["TREND_UP"]["cum_pnl_pct"] == 2.0
        assert b["RANGE"]["count"] == 1


class TestSetupConversion:
    def test_conversion_rate(self):
        snaps = [
            _snap("S1", setup_type="TREND_CONTINUATION"),
            _snap("S2", setup_type="TREND_CONTINUATION"),
            _snap("S3", setup_type="RETEST_REIGNITION"),
            _snap("S4", setup_type="RETEST_REIGNITION"),
        ]
        queues = [_queue(f"S{i}") for i in range(1, 5)]
        results = [
            _rec("S1", exit_reason="TP1_HIT"),
            _rec("S2", exit_reason="STOP_HIT"),
        ]
        sc = SimulationStatistics.compute(snaps, queues, results)["setup_conversion"]
        assert sc["TREND_CONTINUATION"]["recommended"] == 2
        assert sc["TREND_CONTINUATION"]["entered"] == 2
        assert sc["TREND_CONTINUATION"]["conversion_rate"] == 1.0
        assert sc["RETEST_REIGNITION"]["recommended"] == 2
        assert sc["RETEST_REIGNITION"]["entered"] == 0
        assert sc["RETEST_REIGNITION"]["conversion_rate"] == 0.0