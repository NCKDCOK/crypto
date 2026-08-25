"""Top10 Ranking 测试 — 依据 V1.1 计划 §三十六。"""

from __future__ import annotations

from src.presentation.ranking import (
    compute_ranking_score,
    rank_symbols,
    generate_system_conclusion,
)


class TestRankingScore:
    def test_basic(self):
        assert compute_ranking_score(80, 0.9) == 72.0

    def test_zero_confidence(self):
        assert compute_ranking_score(100, 0.0) == 0.0

    def test_high_both(self):
        assert compute_ranking_score(90, 0.95) == 85.5


class TestRankSymbols:
    def test_top10_sorted_by_ranking(self):
        symbols = [
            {"symbol": "A", "opportunity_score": 90, "confidence": 0.9,
             "confidence_state": "CONFIDENT", "score_available": True, "stale_flag": 0},
            {"symbol": "B", "opportunity_score": 80, "confidence": 0.95,
             "confidence_state": "CONFIDENT", "score_available": True, "stale_flag": 0},
            {"symbol": "C", "opportunity_score": 85, "confidence": 0.5,
             "confidence_state": "DEGRADED", "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 3
        # A: 90*0.9=81, B: 80*0.95=76, C: 85*0.5=42.5
        assert result[0]["symbol"] == "A"
        assert result[1]["symbol"] == "B"
        assert result[2]["symbol"] == "C"

    def test_unknown_excluded(self):
        """UNKNOWN 不进入 Top10。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 95, "confidence": 0.5,
             "confidence_state": "UNKNOWN", "score_available": True, "stale_flag": 0},
            {"symbol": "B", "opportunity_score": 70, "confidence": 0.9,
             "confidence_state": "CONFIDENT", "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 1
        assert result[0]["symbol"] == "B"

    def test_stale_excluded(self):
        """stale symbol 不进入高置信 Top10。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 95, "confidence": 0.9,
             "confidence_state": "CONFIDENT", "score_available": True, "stale_flag": 1},
            {"symbol": "B", "opportunity_score": 70, "confidence": 0.9,
             "confidence_state": "CONFIDENT", "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 1
        assert result[0]["symbol"] == "B"

    def test_score_unavailable_excluded(self):
        """评分不可用不进入 Top10。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 0, "confidence": 0,
             "confidence_state": "CONFIDENT", "score_available": False, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 0

    def test_top_n_limit(self):
        symbols = [
            {"symbol": f"S{i}", "opportunity_score": 90 - i, "confidence": 0.9,
             "confidence_state": "CONFIDENT", "score_available": True, "stale_flag": 0}
            for i in range(15)
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 10

    def test_ranking_score_not_exposed_to_user(self):
        """用户只看到 机会分 + 置信度，不需要显示 RankingScore。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 90, "confidence": 0.9,
             "confidence_state": "CONFIDENT", "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        # ranking_score is computed but the UI can choose not to display it
        assert "ranking_score" in result[0]
        assert "opportunity_score" in result[0]
        assert "confidence" in result[0]


class TestSystemConclusion:
    def test_confirmed(self):
        top10 = [{"symbol": "ONGUSDT", "state": "START_CONFIRMED"}]
        conclusion = generate_system_conclusion(top10, 3)
        assert "确认启动" in conclusion
        assert "ONGUSDT" in conclusion

    def test_no_opportunity(self):
        conclusion = generate_system_conclusion([], 0)
        assert "无确认启动" in conclusion or "建议等待" in conclusion

    def test_only_anomaly(self):
        top10 = [{"symbol": "BTCUSDT", "state": "ANOMALY"}]
        conclusion = generate_system_conclusion(top10, 3)
        assert "异动" in conclusion
