"""Top10 Ranking — 按 RankingScore 排名。

依据：V1.1 计划 §十六, V1.2 计划 §二十六
V1.2: RankingScore = OpportunityScore × SignalConfirmation × DataConfidence
- 三个分独立相乘（机会 × 确认 × 可信）
- 首页 Top10 按 RankingScore 排
- 用户只看到 机会分 / 信号确认 / 数据可信，不显示 RankingScore
- UNKNOWN / stale symbol 不进入 Top10
- V1.2 §6.4: 排名滞回由 RankingHysteresis 维护（P3），本函数仅做无状态打分排序
"""

from __future__ import annotations

from typing import Any

from src.domain import ConfidenceState, State


def compute_ranking_score(
    opportunity_score: float,
    signal_confirmation: float,
    data_confidence: float,
) -> float:
    """RankingScore = Opportunity × (SignalConf/100) × (DataConf/100)。

    三者范围均为 0~100。结果为 0~100 的可排序标量。
    """
    return opportunity_score * (signal_confirmation / 100.0) * (data_confidence / 100.0)


def rank_symbols(
    symbols: list[dict[str, Any]],
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """对 symbol 列表按 RankingScore 排名，返回 Top N。

    排除规则：
    - confidence_state == UNKNOWN → 不进入 Top10
    - 评分不可用 (score_available == False) → 不进入 Top10
    - stale (stale_flag == 1) → 不进入 Top10
    - data_confidence / signal_confirmation 缺失 → 不进入 Top10
    """
    eligible = []
    for s in symbols:
        # 排除 UNKNOWN
        conf_state = s.get("confidence_state", "UNKNOWN")
        if conf_state == "UNKNOWN":
            continue
        # 排除评分不可用
        if not s.get("score_available", False):
            continue
        # 排除 stale
        if s.get("stale_flag", 0) == 1:
            continue

        opp = s.get("opportunity_score", 0) or 0
        sc = s.get("signal_confirmation")
        dc = s.get("data_confidence")
        # 兼容：V1.1 旧字段 confidence(0~1) → 映射为 data_confidence
        if dc is None:
            dc = (s.get("confidence", 0) or 0) * 100.0
        if sc is None:
            sc = dc  # 信号确认缺失时退化为数据可信（保守）
        if dc is None or sc is None:
            continue

        ranking = compute_ranking_score(opp, sc, dc)
        eligible.append({**s, "ranking_score": ranking})

    # 按 RankingScore 降序
    eligible.sort(key=lambda x: x["ranking_score"], reverse=True)

    return eligible[:top_n]


def generate_system_conclusion(top10: list[dict[str, Any]], total_candidates: int) -> str:
    """生成系统结论（规则生成，非大模型自由发挥）。

    依据：V1.1 计划 §三
    """
    confirmed = [s for s in top10 if s.get("state") == "START_CONFIRMED"]
    continuation = [s for s in top10 if s.get("state") == "CONTINUATION"]
    suspected = [s for s in top10 if s.get("state") == "SUSPECTED_START"]
    anomaly = [s for s in top10 if s.get("state") == "ANOMALY"]

    if confirmed:
        top_sym = confirmed[0].get("symbol", "")
        return f"当前发现 {len(confirmed)} 个确认启动机会，优先关注 {top_sym}。"
    if continuation:
        top_sym = continuation[0].get("symbol", "")
        return f"当前有 {len(continuation)} 个趋势延续中的标的，关注 {top_sym}。"
    if suspected:
        return f"当前有 {len(suspected)} 个疑似启动，尚在等待确认。"
    if anomaly:
        return f"当前有 {len(anomaly)} 个异动候选正在观察，尚未通过资金确认与假启动过滤。"
    if total_candidates > 0:
        return f"当前有 {total_candidates} 个异动币正在观察，尚未通过启动确认。"
    return "当前无确认启动机会，建议等待。"
