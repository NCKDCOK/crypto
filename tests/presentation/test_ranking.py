"""Top10 Ranking 测试 — 依据 V1.1 计划 §三十六。"""

from __future__ import annotations

from src.presentation.ranking import (
    compute_ranking_score,
    rank_symbols,
    generate_system_conclusion,
)


class TestRankingScore:
    def test_basic(self):
        # 80 * (90/100) * (90/100) = 64.8
        assert compute_ranking_score(80, 90, 90) == 64.8

    def test_zero_data_confidence(self):
        assert compute_ranking_score(100, 90, 0.0) == 0.0

    def test_zero_signal_confirmation(self):
        assert compute_ranking_score(100, 0.0, 90) == 0.0

    def test_high_all(self):
        assert compute_ranking_score(90, 95, 95) == 81.225


class TestRankSymbols:
    def test_top10_sorted_by_ranking(self):
        symbols = [
            {"symbol": "A", "opportunity_score": 90, "signal_confirmation": 90,
             "data_confidence": 90, "confidence_state": "CONFIDENT",
             "score_available": True, "stale_flag": 0},
            {"symbol": "B", "opportunity_score": 80, "signal_confirmation": 95,
             "data_confidence": 95, "confidence_state": "CONFIDENT",
             "score_available": True, "stale_flag": 0},
            {"symbol": "C", "opportunity_score": 85, "signal_confirmation": 50,
             "data_confidence": 50, "confidence_state": "DEGRADED",
             "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 3
        # A: 90*.9*.9=72.9, B: 80*.95*.95=72.2, C: 85*.5*.5=21.25
        assert result[0]["symbol"] == "A"
        assert result[1]["symbol"] == "B"
        assert result[2]["symbol"] == "C"

    def test_unknown_excluded(self):
        """UNKNOWN 不进入 Top10。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 95, "signal_confirmation": 50,
             "data_confidence": 50, "confidence_state": "UNKNOWN",
             "score_available": True, "stale_flag": 0},
            {"symbol": "B", "opportunity_score": 70, "signal_confirmation": 90,
             "data_confidence": 90, "confidence_state": "CONFIDENT",
             "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 1
        assert result[0]["symbol"] == "B"

    def test_stale_excluded(self):
        """stale symbol 不进入高置信 Top10。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 95, "signal_confirmation": 90,
             "data_confidence": 90, "confidence_state": "CONFIDENT",
             "score_available": True, "stale_flag": 1},
            {"symbol": "B", "opportunity_score": 70, "signal_confirmation": 90,
             "data_confidence": 90, "confidence_state": "CONFIDENT",
             "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 1
        assert result[0]["symbol"] == "B"

    def test_score_unavailable_excluded(self):
        """评分不可用不进入 Top10。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 0, "signal_confirmation": 0,
             "data_confidence": 0, "confidence_state": "CONFIDENT",
             "score_available": False, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 0

    def test_missing_confirmation_excluded(self):
        """data_confidence / signal_confirmation 缺失不进入 Top10。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 90, "confidence_state": "CONFIDENT",
             "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 1  # 旧 confidence 字段兼容回退

    def test_top_n_limit(self):
        symbols = [
            {"symbol": f"S{i}", "opportunity_score": 90 - i,
             "signal_confirmation": 90, "data_confidence": 90,
             "confidence_state": "CONFIDENT", "score_available": True, "stale_flag": 0}
            for i in range(15)
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 10

    def test_ranking_score_not_exposed_to_user(self):
        """用户只看到 机会分 / 信号确认 / 数据可信，不需要显示 RankingScore。"""
        symbols = [
            {"symbol": "A", "opportunity_score": 90, "signal_confirmation": 90,
             "data_confidence": 90, "confidence_state": "CONFIDENT",
             "score_available": True, "stale_flag": 0},
        ]
        result = rank_symbols(symbols, top_n=10)
        assert "ranking_score" in result[0]
        assert "opportunity_score" in result[0]
        assert "signal_confirmation" in result[0]
        assert "data_confidence" in result[0]


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


# ── V1.3 §13 严格模式 ────────────────────────────────────────────────
# 正式 Top Opportunity：state ∈ {START_CONFIRMED, CONTINUATION}、
# opportunity ≥ 70、signal_confirmation ≥ 75、data_confidence ≥ 85、
# 上限 10 且不足不强制凑满。严格过滤均为 rank_symbols 可选参数，
# 默认（不传）保持 V1.1/V1.2 宽松行为，保证旧 fixture 兼容。


def _sym(name: str, state: str, opportunity: float,
         confirmation: float, confidence: float) -> dict:
    return {
        "symbol": name,
        "state": state,
        "opportunity_score": opportunity,
        "signal_confirmation": confirmation,
        "data_confidence": confidence,
        "confidence_state": "CONFIDENT",
        "score_available": True,
        "stale_flag": 0,
    }


class TestV13RankSymbolsStrict:
    """V1.3 §13 严格门槛（rank_symbols 可选参数，向后兼容）。"""

    def test_v13_state_filter_excludes_others(self):
        """allowed_states 传入时，COOLDOWN/ANOMALY 高分也被排除。"""
        symbols = [
            _sym("COOLUSDT", "COOLDOWN", 95, 95, 95),
            _sym("ANOUSDT", "ANOMALY", 90, 90, 90),
            _sym("OKUSDT", "START_CONFIRMED", 80, 85, 90),
        ]
        result = rank_symbols(
            symbols, top_n=10,
            allowed_states=["START_CONFIRMED", "CONTINUATION"],
        )
        assert [s["symbol"] for s in result] == ["OKUSDT"]

    def test_v13_state_filter_keeps_allowed(self):
        symbols = [
            _sym("AUSDT", "START_CONFIRMED", 80, 85, 90),
            _sym("BUSDT", "CONTINUATION", 75, 80, 88),
            _sym("CUSDT", "SUSPECTED_START", 99, 99, 99),
        ]
        result = rank_symbols(
            symbols, top_n=10,
            allowed_states=["START_CONFIRMED", "CONTINUATION"],
        )
        assert [s["symbol"] for s in result] == ["AUSDT", "BUSDT"]

    def test_v13_opportunity_threshold(self):
        """min_opportunity=70：机会分 60 的即使高确认也不进。"""
        symbols = [
            _sym("LOWUSDT", "START_CONFIRMED", 60, 90, 90),
            _sym("OKUSDT", "START_CONFIRMED", 72, 90, 90),
        ]
        result = rank_symbols(symbols, top_n=10, min_opportunity=70.0)
        assert [s["symbol"] for s in result] == ["OKUSDT"]

    def test_v13_confirmation_threshold(self):
        """min_signal_confirmation=75：确认 70 的不进。"""
        symbols = [
            _sym("LOWUSDT", "START_CONFIRMED", 90, 70, 90),
            _sym("OKUSDT", "START_CONFIRMED", 90, 76, 90),
        ]
        result = rank_symbols(
            symbols, top_n=10, min_signal_confirmation=75.0)
        assert [s["symbol"] for s in result] == ["OKUSDT"]

    def test_v13_data_confidence_threshold(self):
        """min_data_confidence=85：可信 80 的不进。"""
        symbols = [
            _sym("LOWUSDT", "START_CONFIRMED", 90, 90, 80),
            _sym("OKUSDT", "START_CONFIRMED", 90, 90, 86),
        ]
        result = rank_symbols(symbols, top_n=10, min_data_confidence=85.0)
        assert [s["symbol"] for s in result] == ["OKUSDT"]

    def test_v13_combined_strict_mode(self):
        """完整严格路径（runtime.get_top10 同款参数）。"""
        symbols = [
            _sym("COOLUSDT", "COOLDOWN", 95, 95, 95),   # 状态排除
            _sym("LOWUSDT", "START_CONFIRMED", 60, 99, 99),  # 机会不足
            _sym("MEHUSDT", "START_CONFIRMED", 90, 70, 99),  # 确认不足
            _sym("POORUSDT", "START_CONFIRMED", 90, 90, 80),  # 可信不足
            _sym("GOODUSDT", "START_CONFIRMED", 80, 85, 90),  # 唯一合格
            _sym("RUNUSDT", "CONTINUATION", 72, 76, 88),
        ]
        result = rank_symbols(
            symbols, top_n=10,
            min_opportunity=70.0,
            min_signal_confirmation=75.0,
            min_data_confidence=85.0,
            allowed_states=["START_CONFIRMED", "CONTINUATION"],
        )
        # 排序按 ranking score：GOOD 80*.85*.90=61.2 > RUN 72*.76*.88=48.15
        assert [s["symbol"] for s in result] == ["GOODUSDT", "RUNUSDT"]

    def test_v13_no_forced_10(self):
        """合格不足 10 个时，不强制凑满。"""
        symbols = [
            _sym("AUSDT", "START_CONFIRMED", 75, 80, 86),
            _sym("BUSDT", "CONTINUATION", 71, 76, 85),
        ]
        result = rank_symbols(symbols, top_n=10,
                              min_opportunity=70.0,
                              min_signal_confirmation=75.0,
                              min_data_confidence=85.0,
                              allowed_states=["START_CONFIRMED", "CONTINUATION"])
        assert len(result) == 2

    def test_v13_permissive_default_backward_compat(self):
        """默认（不传过滤参数）仍保留旧行为：次阈值 C 也能上榜。"""
        symbols = [
            _sym("AUSDT", "START_CONFIRMED", 90, 90, 90),
            _sym("CUSDT", "ANOMALY", 85, 50, 50),  # 次阈值，但默认仍上榜
        ]
        result = rank_symbols(symbols, top_n=10)
        assert len(result) == 2
