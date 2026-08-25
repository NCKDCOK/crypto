"""Rolling window 测试 — 边界、淘汰、多窗口。"""

from __future__ import annotations

from src.windows.rolling_window import RollingWindow, WindowManager


class TestRollingWindow:
    def test_add_and_get(self):
        w = RollingWindow[int](window_ms=5000)
        w.add(1000, "a")
        w.add(2000, "b")
        assert w.get_items(2500) == ["a", "b"]

    def test_evict_old_items(self):
        """窗口外的元素被淘汰。cutoff = now - window_ms, < cutoff 的淘汰。"""
        w = RollingWindow[int](window_ms=5000)
        w.add(1000, "a")
        w.add(2000, "b")
        w.add(8000, "c")  # 8000 - 5000 = 3000, a(1000) 和 b(2000) 都 < 3000 被淘汰
        items = w.get_items(8000)
        assert "a" not in items
        assert "b" not in items  # 2000 < 3000 也被淘汰
        assert "c" in items

    def test_empty_window(self):
        w = RollingWindow[int](window_ms=5000)
        assert w.is_empty(1000)
        assert w.get_items(1000) == []
        assert w.count(1000) == 0

    def test_single_item(self):
        w = RollingWindow[int](window_ms=5000)
        w.add(1000, "x")
        assert w.count(1000) == 1
        assert w.get_items(1000) == ["x"]

    def test_clear(self):
        w = RollingWindow[int](window_ms=5000)
        w.add(1000, "a")
        w.clear()
        assert w.is_empty(1000)

    def test_earliest_latest_time(self):
        w = RollingWindow[int](window_ms=10000)
        w.add(1000, "a")
        w.add(3000, "b")
        w.add(5000, "c")
        assert w.earliest_time == 1000
        assert w.latest_time == 5000


class TestWindowManager:
    def test_multi_window_add(self):
        wm = WindowManager([5000, 10000])
        wm.add(1000, "a")
        wm.add(3000, "b")

        # 5s 窗口在 t=7000 时淘汰（cutoff=2000, a(1000) 被淘汰）
        items_5s = wm.get_items(5000, 7000)
        assert "a" not in items_5s  # 1000 < 2000 被淘汰
        assert "b" in items_5s      # 3000 >= 2000 保留

        # 10s 窗口在 t=7000 时仍保留（cutoff=-3000）
        items_10s = wm.get_items(10000, 7000)
        assert "a" in items_10s
        assert "b" in items_10s

    def test_clear_all(self):
        wm = WindowManager([5000, 10000])
        wm.add(1000, "a")
        wm.clear()
        assert wm.get_items(5000, 1000) == []
