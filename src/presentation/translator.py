"""Presentation Translator — 内部术语 → 用户中文翻译层。

依据：V1.1 计划 §十七~§二十二
职责：把技术字段翻译成普通用户能看懂的中文。
- 状态翻译
- 方向翻译
- 数据健康翻译
- 子评分翻译
- Evidence 翻译
- Veto 翻译
- 一句话结论生成
- "还缺什么" 提示
"""

from __future__ import annotations

from typing import Any

from src.domain import (
    ConfidenceState,
    Direction,
    Evidence,
    State,
    Veto,
    VetoType,
)
from src.scoring.engine import ScoreBreakdown, SubScore
from src.scoring.data_confidence import DataConfidenceBreakdown
from src.scoring.signal_confirmation import SignalConfirmationBreakdown

# ── 状态映射 ──
STATE_LABELS: dict[str, str] = {
    "SLEEPING": "沉睡",
    "ANOMALY": "发现异动",
    "SUSPECTED_START": "等待确认",
    "START_CONFIRMED": "启动确认",
    "CONTINUATION": "趋势延续",
    "EXHAUSTION": "动能衰竭",
    "WITHDRAWAL": "资金撤离",
    "REJECTED": "已拒绝",
    "COOLDOWN": "冷却中",
}

STATE_EMOJI: dict[str, str] = {
    "SLEEPING": "😴",
    "ANOMALY": "🔍",
    "SUSPECTED_START": "⏳",
    "START_CONFIRMED": "🚀",
    "CONTINUATION": "📈",
    "EXHAUSTION": "⚠️",
    "WITHDRAWAL": "📉",
    "REJECTED": "✖️",
    "COOLDOWN": "❄️",
}

# ── 方向映射 ──
DIRECTION_LABELS: dict[str, str] = {
    "LONG": "做多",
    "SHORT": "做空",
    "NEUTRAL": "中性",
}

# ── 置信度映射 ──
CONFIDENCE_LABELS: dict[str, str] = {
    "CONFIDENT": "数据可信",
    "DEGRADED": "部分数据降级",
    "UNKNOWN": "数据不足",
}

# ── 数据健康映射 ──
HEALTH_LABELS: dict[str, str] = {
    "OK": "正常",
    "WARN": "预热中",
    "STALE": "数据延迟",
    "DRIFT": "数据偏移",
    "FAIL": "数据异常",
}

# ── Veto 类型映射 ──
VETO_LABELS: dict[str, str] = {
    "data_stale": "数据过期",
    "rapid_retrace": "快速回吐",
    "oi_contraction": "OI 收缩",
    "delta_reversal": "主动资金方向反转",
    "no_acceptance": "突破未站稳",
    "low_efficiency_absorption": "放量但价格推不动",
    "crowding_extreme": "拥挤度过高",
    "one_bar_spike": "单根插针",
}

# ── Evidence 类型映射 ──
EVIDENCE_LABELS: dict[str, str] = {
    "volume_z": "成交量 Z",
    "trade_count_z": "成交笔数 Z",
    "taker_delta_z": "主动买卖 Z",
    "price_accel_z": "价格加速 Z",
    "oi_expansion": "OI 扩张",
    "oi_change": "OI 变化",
    "flow_confirmation": "资金流确认",
    "breakout_acceptance": "突破有效性",
    "cvd_slope": "CVD 斜率",
    "efficiency": "价格效率",
}


class PresentationTranslator:
    """翻译层 — 把内部术语翻译成普通用户能看懂的中文。"""

    @staticmethod
    def state_label(state: str | State) -> str:
        s = state.value if isinstance(state, State) else str(state)
        return STATE_LABELS.get(s, s)

    @staticmethod
    def state_emoji(state: str | State) -> str:
        s = state.value if isinstance(state, State) else str(state)
        return STATE_EMOJI.get(s, "")

    @staticmethod
    def state_display(state: str | State) -> str:
        """带 emoji 的状态显示。"""
        emoji = PresentationTranslator.state_emoji(state)
        label = PresentationTranslator.state_label(state)
        return f"{emoji} {label}" if emoji else label

    @staticmethod
    def direction_label(direction: str | Direction | None) -> str:
        if direction is None:
            return "未定"
        d = direction.value if isinstance(direction, Direction) else str(direction)
        return DIRECTION_LABELS.get(d, d)

    @staticmethod
    def confidence_label(confidence: str | ConfidenceState) -> str:
        c = confidence.value if isinstance(confidence, ConfidenceState) else str(confidence)
        return CONFIDENCE_LABELS.get(c, c)

    @staticmethod
    def health_label(status: str) -> str:
        return HEALTH_LABELS.get(status, status)

    @staticmethod
    def data_status_label(
        confidence_state: str | ConfidenceState,
        any_stale: bool = False,
        any_fail: bool = False,
    ) -> str:
        """整体数据状态标签（用户首页用）。"""
        if any_fail:
            return "数据异常"
        if any_stale:
            return "数据延迟"
        c = confidence_state.value if isinstance(confidence_state, ConfidenceState) else str(confidence_state)
        if c == "UNKNOWN":
            return "数据异常"
        if c == "DEGRADED":
            return "数据降级"
        return "数据正常"

    @staticmethod
    def veto_label(veto_type: str | VetoType) -> str:
        v = veto_type.value if isinstance(veto_type, VetoType) else str(veto_type)
        return VETO_LABELS.get(v, v)

    @staticmethod
    def evidence_label(ev_type: str) -> str:
        return EVIDENCE_LABELS.get(ev_type, ev_type)

    # ── 资金行为模块翻译 ──

    @staticmethod
    def translate_capital_flow(fv: dict[str, Any]) -> dict[str, str]:
        """资金行为模块 — 用户层翻译。"""
        return {
            "主动买盘": PresentationTranslator._strength_label(
                fv.get("taker_buy_volume"), fv.get("taker_sell_volume"), "buy"
            ),
            "新增仓位": PresentationTranslator._oi_label(fv.get("oi_change_5m")),
            "资金持续性": PresentationTranslator._persistence_label(fv.get("cvd_slope_z")),
            "拥挤程度": PresentationTranslator._crowding_label(fv.get("funding_percentile")),
            "撤离迹象": PresentationTranslator._withdrawal_label(fv.get("oi_change_5m"), fv.get("signed_delta")),
        }

    @staticmethod
    def translate_volume_price(fv: dict[str, Any]) -> dict[str, str]:
        """量价模块 — 用户层翻译。"""
        return {
            "成交量": PresentationTranslator._volume_label(fv.get("volume_z")),
            "价格推动效率": PresentationTranslator._efficiency_label(fv.get("price_efficiency")),
            "回踩承接": PresentationTranslator._retrace_label(fv.get("retrace_ratio")),
            "突破有效性": PresentationTranslator._acceptance_label(fv.get("acceptance")),
        }

    @staticmethod
    def translate_false_start_check(vetoes: list[dict[str, Any]]) -> list[dict[str, str]]:
        """假启动检查 — 用户层翻译。"""
        results = []
        veto_map = {
            "rapid_retrace": "无快速回吐",
            "oi_contraction": "OI 未收缩",
            "delta_reversal": "主动买盘未反转",
            "no_acceptance": "突破后仍能站稳",
            "low_efficiency_absorption": "未出现明显放量滞涨",
            "crowding_extreme": "拥挤度正常",
            "one_bar_spike": "未出现单根插针",
            "data_stale": "数据正常",
        }
        triggered_types = {v.get("type") for v in vetoes if v.get("triggered")}
        for vtype, label in veto_map.items():
            passed = vtype not in triggered_types
            results.append({
                "check": label,
                "passed": passed,
                "display": f"{'✅' if passed else '❌'} {label}",
            })
        return results

    # ── 一句话结论 ──

    @staticmethod
    def generate_summary(
        state: str | State,
        direction: str | None,
        score: ScoreBreakdown | None,
        data_confidence: DataConfidenceBreakdown | None = None,
        signal_confirmation: SignalConfirmationBreakdown | None = None,
    ) -> str:
        """生成用户能看懂的一句话结论。

        V1.2：data_confidence（数据可信）/ signal_confirmation（信号确认度）
        仅作为「确认度」语境提示，绝不表述为「胜率」。
        """
        s = state.value if isinstance(state, State) else str(state)
        state_label = PresentationTranslator.state_label(s)

        if s in ("SLEEPING", "COOLDOWN", "REJECTED"):
            return f"当前处于{state_label}状态，暂无活跃信号。"

        if s == "ANOMALY":
            return "检测到异常成交活动，正在观察是否形成启动信号。"

        if s == "SUSPECTED_START":
            missing = PresentationTranslator.missing_confirmations(score)
            if missing:
                return f"疑似启动中，还缺 {len(missing)} 项确认：{'、'.join(missing)}。"
            return "疑似启动中，等待最终确认。"

        if s == "START_CONFIRMED":
            dir_label = PresentationTranslator.direction_label(direction)
            parts = []
            if score and score.available:
                subs = score.subscores
                ci = subs.get("capital_inflow")
                sq = subs.get("startup_quality")
                if ci and ci.score > 70:
                    parts.append("资金持续进入")
                if sq and sq.score > 70:
                    parts.append("启动质量良好")
                wr = subs.get("withdrawal_risk")
                if wr and wr.score < 30:
                    parts.append("暂无撤离迹象")
            if signal_confirmation and signal_confirmation.available and signal_confirmation.strong_confirm:
                parts.append("证据强确认")
            if not parts:
                parts.append("启动信号已确认")
            return f"属于{dir_label}启动阶段，{'，'.join(parts)}。"

        if s == "CONTINUATION":
            dir_label = PresentationTranslator.direction_label(direction)
            return f"趋势延续中，{dir_label}方向资金仍在维持。"

        if s == "EXHAUSTION":
            return "动能开始衰竭，买盘推动效率下降，注意风险。"

        if s == "WITHDRAWAL":
            return "资金正在撤离，OI 和主动买盘同步减弱。"

        return f"当前状态：{state_label}。"

    @staticmethod
    def missing_confirmations(score: ScoreBreakdown | None) -> list[str]:
        """SUSPECTED_START 状态下还缺什么确认。"""
        if not score or not score.available:
            return ["资金确认", "假启动过滤"]
        missing = []
        subs = score.subscores
        ci = subs.get("capital_inflow")
        if ci is None or ci.score < 60:
            missing.append("OI 持续扩张")
        sq = subs.get("startup_quality")
        if sq is None or sq.score < 60:
            missing.append("第二波主动买盘确认")
        return missing

    # ── 内部翻译辅助 ──

    @staticmethod
    def _strength_label(buy: float | None, sell: float | None, side: str = "buy") -> str:
        if buy is None or sell is None:
            return "数据不足"
        if side == "buy":
            if buy > sell * 1.5:
                return "强"
            if buy > sell * 1.1:
                return "偏强"
            if buy < sell * 0.7:
                return "弱"
            return "均衡"
        return "均衡"

    @staticmethod
    def _oi_label(oi_change: float | None) -> str:
        if oi_change is None:
            return "数据不足"
        if oi_change > 0.03:
            return "明显增加"
        if oi_change > 0.01:
            return "小幅增加"
        if oi_change < -0.03:
            return "明显减少"
        if oi_change < -0.01:
            return "小幅减少"
        return "稳定"

    @staticmethod
    def _persistence_label(cvd_z: float | None) -> str:
        if cvd_z is None:
            return "数据不足"
        if abs(cvd_z) > 2:
            return "良好"
        if abs(cvd_z) > 1:
            return "一般"
        return "偏弱"

    @staticmethod
    def _crowding_label(funding_pct: float | None) -> str:
        if funding_pct is None:
            return "数据不足"
        if funding_pct > 90:
            return "高"
        if funding_pct > 70:
            return "偏高"
        if funding_pct < 30:
            return "低"
        return "正常"

    @staticmethod
    def _withdrawal_label(oi_change: float | None, delta: float | None) -> str:
        if oi_change is not None and oi_change < -0.02:
            return "有撤离迹象"
        if delta is not None and delta < 0:
            return "资金方向反转"
        return "暂无"

    @staticmethod
    def _volume_label(vol_z: float | None) -> str:
        if vol_z is None:
            return "数据不足"
        if vol_z > 3:
            return "明显放大"
        if vol_z > 2:
            return "放大"
        if vol_z < 1:
            return "正常"
        return "温和放大"

    @staticmethod
    def _efficiency_label(eff: float | None) -> str:
        if eff is None:
            return "数据不足"
        if eff > 0.6:
            return "健康"
        if eff > 0.3:
            return "一般"
        return "偏低"

    @staticmethod
    def _retrace_label(retrace: float | None) -> str:
        if retrace is None:
            return "数据不足"
        if retrace < 0.3:
            return "良好"
        if retrace < 0.6:
            return "一般"
        return "较差"

    @staticmethod
    def _acceptance_label(accept: float | None) -> str:
        if accept is None:
            return "数据不足"
        if accept > 0.7:
            return "已确认"
        if accept > 0.4:
            return "部分确认"
        return "未确认"

    # ── 状态时间轴翻译 ──

    @staticmethod
    def translate_timeline(transitions: list[dict[str, Any]]) -> list[dict[str, str]]:
        """状态时间轴翻译。"""
        result = []
        for t in transitions:
            result.append({
                "time": t.get("asof"),
                "state": PresentationTranslator.state_display(t.get("new_state") or t.get("state", "")),
                "state_label": PresentationTranslator.state_label(t.get("new_state") or t.get("state", "")),
            })
        return result

    # ── 子评分用户标签 ──

    @staticmethod
    def subscore_labels() -> dict[str, str]:
        """子评分内部 key → 用户中文标签。"""
        return {
            "capital_inflow": "资金输入",
            "startup_quality": "启动质量",
            "trend": "趋势",
            "immediate_stamina": "即时续航",
            "sustained_startup": "持续启动",
            "anomaly_intensity": "异动强度",
            "chase_safety": "追涨安全",
            "top_risk": "顶部风险",
            "crowding_risk": "拥挤风险",
            "withdrawal_risk": "撤离风险",
            "chase_risk": "追涨风险",
        }
