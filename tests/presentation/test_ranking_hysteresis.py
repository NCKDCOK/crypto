"""Ranking Hysteresis 测试 — V1.2 §6.4。"""

from __future__ import annotations

from src.presentation.ranking_hysteresis import RankingHysteresis


def _ranked(scores: dict[str, float]) -> list[dict]:
    return [{"symbol": s, "ranking_score": v} for s, v in scores.items()]


class TestRankingHysteresis:
    def test_first_call_passthrough(self):
        h = RankingHysteresis(rerank_interval_s=30)
        r = h.update(_ranked({"A": 80, "B": 70, "C": 60}), now_ms=0)
        assert [x["symbol"] for x in r] == ["A", "B", "C"]

    def test_order_frozen_within_interval(self):
        """30s 内顺序冻结（即使分数变化）。"""
        h = RankingHysteresis(rerank_interval_s=30)
        h.update(_ranked({"A": 80, "B": 70}), now_ms=0)
        # B 涨到 85 但未到 30s → 顺序不变
        r = h.update(_ranked({"A": 80, "B": 85}), now_ms=10_000)
        assert [x["symbol"] for x in r] == ["A", "B"]

    def test_rerank_after_interval(self):
        h = RankingHysteresis(rerank_interval_s=30)
        h.update(_ranked({"A": 80, "B": 70}), now_ms=0)
        # 30s 后重排，B 大幅领先（分差>3）→ 交换
        r = h.update(_ranked({"A": 80, "B": 90}), now_ms=31_000)
        assert [x["symbol"] for x in r] == ["B", "A"]

    def test_small_diff_no_swap(self):
        """分差 <=3 不交换。"""
        h = RankingHysteresis(rerank_interval_s=30, swap_score_diff=3.0, streak_threshold=2)
        h.update(_ranked({"A": 80, "B": 79}), now_ms=0)
        # B 微弱领先 1 分（<3）→ 不交换
        r = h.update(_ranked({"A": 80, "B": 81}), now_ms=31_000)
        assert [x["symbol"] for x in r] == ["A", "B"]

    def test_streak_two_rounds_swap(self):
        """连续 2 轮领先（即使分差小）→ 交换。"""
        h = RankingHysteresis(rerank_interval_s=30, swap_score_diff=3.0, streak_threshold=2)
        h.update(_ranked({"A": 80, "B": 79}), now_ms=0)
        # 第 1 轮：B 领先 1 分（<3）→ 不交换，streak=1
        h.update(_ranked({"A": 80, "B": 81}), now_ms=31_000)
        # 第 2 轮：B 仍领先 → streak=2 → 交换
        r = h.update(_ranked({"A": 80, "B": 81}), now_ms=62_000)
        assert [x["symbol"] for x in r] == ["B", "A"]

    def test_new_symbol_appended(self):
        h = RankingHysteresis(rerank_interval_s=30)
        h.update(_ranked({"A": 80, "B": 70}), now_ms=0)
        r = h.update(_ranked({"A": 80, "B": 70, "C": 90}), now_ms=31_000)
        # C 新进入，分最高；首入追加位置（下一轮才可能上升到顶部）
        syms = [x["symbol"] for x in r]
        assert "C" in syms

    def test_fresh_data_preserved_in_frozen_order(self):
        """冻结顺序内数据保持最新。"""
        h = RankingHysteresis(rerank_interval_s=30)
        h.update(_ranked({"A": 80, "B": 70}), now_ms=0)
        r = h.update(_ranked({"A": 82, "B": 68}), now_ms=10_000)
        a = next(x for x in r if x["symbol"] == "A")
        assert a["ranking_score"] == 82  # 最新分数
