"""Rolling time window buffer — 按 receive_time 滚动，超窗口淘汰。

依据：ANALYSIS_MODEL.md §1
窗口层级：5s/15s/30s/1m/3m/5m/15m/1h/4h/24h
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class RollingWindow(Generic[T]):
    """时间滚动窗口。

    按 receive_time 淘汰过期元素。窗口大小以毫秒计。
    """

    window_ms: int
    _buffer: deque[tuple[int, T]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._buffer = deque()

    def add(self, receive_time: int, item: T) -> None:
        """添加元素，并淘汰过期元素。"""
        self._buffer.append((receive_time, item))
        self._evict(receive_time)

    def _evict(self, now: int) -> None:
        """淘汰窗口外的元素。"""
        cutoff = now - self.window_ms
        while self._buffer and self._buffer[0][0] < cutoff:
            self._buffer.popleft()

    def get_items(self, now: int) -> list[T]:
        """获取窗口内所有有效元素（淘汰后）。"""
        self._evict(now)
        return [item for _, item in self._buffer]

    def get_timestamped(self, now: int) -> list[tuple[int, T]]:
        """获取窗口内所有 (timestamp, item) 对。"""
        self._evict(now)
        return list(self._buffer)

    def count(self, now: int) -> int:
        self._evict(now)
        return len(self._buffer)

    def is_empty(self, now: int) -> bool:
        return self.count(now) == 0

    def clear(self) -> None:
        self._buffer.clear()

    @property
    def earliest_time(self) -> int | None:
        """窗口中最早的时间戳，空则 None。"""
        return self._buffer[0][0] if self._buffer else None

    @property
    def latest_time(self) -> int | None:
        """窗口中最晚的时间戳，空则 None。"""
        return self._buffer[-1][0] if self._buffer else None


class WindowManager:
    """管理多个窗口尺寸的滚动缓冲。

    一个 symbol 可有多个窗口（5s/15s/30s/1m/5m 等），
    统一添加事件、各自淘汰。
    """

    def __init__(self, window_sizes_ms: list[int]) -> None:
        self.windows: dict[int, RollingWindow] = {
            ms: RollingWindow(ms) for ms in window_sizes_ms
        }

    def add(self, receive_time: int, item) -> None:
        """向所有窗口添加同一事件。"""
        for w in self.windows.values():
            w.add(receive_time, item)

    def get_window(self, window_ms: int) -> RollingWindow | None:
        return self.windows.get(window_ms)

    def get_items(self, window_ms: int, now: int) -> list:
        w = self.windows.get(window_ms)
        return w.get_items(now) if w else []

    def clear(self) -> None:
        for w in self.windows.values():
            w.clear()
