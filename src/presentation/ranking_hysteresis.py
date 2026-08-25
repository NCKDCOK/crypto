"""Ranking Hysteresis — Top10 排名滞回（V1.2 §6.4）。

首页 Top10 重新排序：30s。
排名滞回：分差 > 3 或连续 2 轮排名领先，才交换位置。

后台仍可实时计算，但用户看到的 Top10 顺序稳定，不会每秒乱跳。
"""

from __future__ import annotations

from typing import Any


class RankingHysteresis:
    """Top10 排名滞回状态机。"""

    def __init__(
        self,
        rerank_interval_s: float = 30.0,
        swap_score_diff: float = 3.0,
        streak_threshold: int = 2,
    ) -> None:
        self.rerank_interval_ms = int(rerank_interval_s * 1000)
        self.swap_diff = swap_score_diff
        self.streak_threshold = streak_threshold
        self.prev_order: list[str] = []
        self.lead_streak: dict[str, int] = {}
        self.last_rerank_ms: int = 0
        self._cached_order: list[str] = []

    def update(self, ranked: list[dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
        """更新 Top10。

        Args:
            ranked: 已按 ranking_score 降序排列的 dict 列表（含 symbol / ranking_score 等）。
            now_ms: 当前时间。

        Returns:
            按「滞回顺序」排列的 dict 列表（数据为最新，顺序稳定）。
        """
        if not ranked:
            self._cached_order = []
            return []

        # 每 rerank_interval 才重新计算顺序；期间顺序冻结但数据保持最新
        rerank = (not self._cached_order) or (now_ms - self.last_rerank_ms >= self.rerank_interval_ms)
        if rerank:
            self._cached_order = self._hysteresis_order(ranked)
            self.last_rerank_ms = now_ms

        # 按 cached_order 重排最新数据（数据最新，顺序稳定）
        order_set = set(self._cached_order)
        by_sym = {r["symbol"]: r for r in ranked}
        result = [by_sym[s] for s in self._cached_order if s in by_sym]
        # 新进入的 symbol（不在 cached_order 中）追加到末尾
        for r in ranked:
            if r["symbol"] not in order_set:
                result.append(r)
        return result[: max(len(result), 0)]

    def _hysteresis_order(self, ranked: list[dict[str, Any]]) -> list[str]:
        new_scores = {r["symbol"]: float(r.get("ranking_score", 0)) for r in ranked}
        # 以 prev_order 为基底（保留仍存在的），新 symbol 追加
        order = [s for s in self.prev_order if s in new_scores]
        for r in ranked:
            if r["symbol"] not in order:
                order.append(r["symbol"])

        # 单趟相邻交换滞回
        for i in range(len(order) - 1):
            a, b = order[i], order[i + 1]
            sa, sb = new_scores.get(a, -1.0), new_scores.get(b, -1.0)
            if sb > sa:
                diff = sb - sa
                if diff > self.swap_diff:
                    order[i], order[i + 1] = b, a
                    self.lead_streak[b] = 0
                else:
                    streak = self.lead_streak.get(b, 0) + 1
                    if streak >= self.streak_threshold:
                        order[i], order[i + 1] = b, a
                        self.lead_streak[b] = 0
                    else:
                        self.lead_streak[b] = streak
            else:
                self.lead_streak[b] = 0

        self.prev_order = order
        return order

    def reset(self) -> None:
        self.prev_order = []
        self.lead_streak = {}
        self.last_rerank_ms = 0
        self._cached_order = []
