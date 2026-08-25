"""AI 解读 — 读取 AnalysisEvent 生成自然语言说明。

依据：SYSTEM_DESIGN.md §16, AI_RULES
LLM 只读 AnalysisEvent 生成自然语言，不得覆盖 new_state / direction / 阈值。
本模块是模板化解读，不调用外部 LLM API。
"""

from __future__ import annotations

from src.domain import AnalysisEvent, State


def generate_summary(event: AnalysisEvent) -> str:
    """生成 AnalysisEvent 的自然语言摘要。

    只读结构化结果翻译成人话，不改变任何状态。
    """
    symbol = event.symbol
    state = event.new_state.value
    direction = event.direction.value if event.direction else "未知方向"
    confidence = event.confidence_state.value

    parts: list[str] = []
    parts.append(f"{symbol} 状态={state} 方向={direction} 置信={confidence}")

    # 证据摘要
    if event.evidence:
        passed_evidence = [e for e in event.evidence if e.passed]
        parts.append(f"通过证据 {len(passed_evidence)}/{len(event.evidence)} 条")
        for e in passed_evidence[:3]:
            val_str = f"{e.value:.2f}" if e.value is not None else "N/A"
            parts.append(f"  [{e.family.value}] {e.type}={val_str}")

    # Veto 摘要
    if event.vetoes:
        triggered = [v for v in event.vetoes if v.triggered]
        if triggered:
            parts.append(f"命中否决 {len(triggered)} 项:")
            for v in triggered:
                parts.append(f"  [{v.severity.value}] {v.type.value}")

    # 状态特定说明
    if state == State.SUSPECTED_START.value:
        parts.append("结论：疑似启动，等待 acceptance / 二次确认。")
    elif state == State.START_CONFIRMED.value:
        parts.append("结论：启动确认，证据链通过，假启动 veto 未命中。")
    elif state == State.REJECTED.value:
        parts.append("结论：假启动被过滤，进入冷却。")
    elif state == State.CONTINUATION.value:
        parts.append("结论：资金仍在持续。")
    elif state == State.EXHAUSTION.value:
        parts.append("结论：推动效率下降，注意风险。")
    elif state == State.WITHDRAWAL.value:
        parts.append("结论：资金撤离确认。")
    elif state == State.ANOMALY.value:
        parts.append("结论：检测到异常，尚未判定方向。")

    return "\n".join(parts)
