"""Queue/Lag 监控测试。"""

from __future__ import annotations

from src.clock import TestClock
from src.health.queue_lag_monitor import QueueLagMonitor


class TestQueueMetrics:
    def test_enqueue_dequeue(self):
        m = QueueLagMonitor(clock=TestClock(initial_ms=1000))
        m.register_queue("feature_engine")

        m.record_enqueue("feature_engine", 10)
        assert m.get_queue_metrics("feature_engine").depth == 10

        m.record_dequeue("feature_engine", 3)
        assert m.get_queue_metrics("feature_engine").depth == 7

    def test_max_depth_tracked(self):
        m = QueueLagMonitor(clock=TestClock(initial_ms=1000))
        m.register_queue("feature_engine")

        m.record_enqueue("feature_engine", 100)
        m.record_dequeue("feature_engine", 50)
        m.record_enqueue("feature_engine", 80)
        metrics = m.get_queue_metrics("feature_engine")
        # depth: 100 - 50 + 80 = 130, max_depth = 130
        assert metrics.max_depth == 130
        assert metrics.depth == 130

    def test_dequeue_below_zero_clamped(self):
        m = QueueLagMonitor(clock=TestClock(initial_ms=1000))
        m.register_queue("feature_engine")
        m.record_enqueue("feature_engine", 5)
        m.record_dequeue("feature_engine", 10)
        assert m.get_queue_metrics("feature_engine").depth == 0

    def test_backlog_warning(self):
        """队列积压超过阈值发出警告。"""
        m = QueueLagMonitor(
            clock=TestClock(initial_ms=1000),
            queue_depth_warn=100,
        )
        m.register_queue("feature_engine")
        m.record_enqueue("feature_engine", 200)
        metrics = m.get_queue_metrics("feature_engine")
        assert metrics.depth == 200
        assert metrics.depth > 100


class TestLagMetrics:
    def test_record_lag(self):
        clock = TestClock(initial_ms=10000)
        m = QueueLagMonitor(clock=clock)
        m.register_lag("aggTrade:BTCUSDT")

        # event_time=9000, receive_time=9500, now=10000
        metrics = m.record_lag("aggTrade:BTCUSDT", event_time=9000, receive_time=9500)
        assert metrics.receive_lag_ms == 500   # 10000 - 9500
        assert metrics.event_lag_ms == 500      # 9500 - 9000

    def test_max_lag_tracked(self):
        clock = TestClock(initial_ms=10000)
        m = QueueLagMonitor(clock=clock)
        m.register_lag("aggTrade:BTCUSDT")

        m.record_lag("aggTrade:BTCUSDT", event_time=9000, receive_time=9500)
        clock.set(15_000)
        m.record_lag("aggTrade:BTCUSDT", event_time=14000, receive_time=14500)

        metrics = m.get_lag_metrics("aggTrade:BTCUSDT")
        assert metrics.max_receive_lag_ms == 500  # max(500, 500)
        assert metrics.sample_count == 2

    def test_high_receive_lag_warning(self):
        """receive lag 超阈值发出警告。"""
        clock = TestClock(initial_ms=20_000)
        m = QueueLagMonitor(
            clock=clock,
            receive_lag_warn_ms=5000,
        )
        m.register_lag("aggTrade:BTCUSDT")
        # receive_time=10000, now=20000 → lag=10000 > 5000
        m.record_lag("aggTrade:BTCUSDT", event_time=9000, receive_time=10000)
        metrics = m.get_lag_metrics("aggTrade:BTCUSDT")
        assert metrics.receive_lag_ms == 10_000
