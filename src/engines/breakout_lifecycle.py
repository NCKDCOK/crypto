"""Breakout Lifecycle Engine — 突破生命周期（V1.2 §15）。

四阶段：
1. 突破：5m 已收盘 K 线站在关键位置外（不能只因为最高价刺穿）
2. 保持：breakout_hold / time_above_level / max_retrace / close_back_inside
3. 回踩：retest_started / retest_low/high / retest_depth / retest_volume / retest_delta / retest_oi
4. 二次确认：回踩没破坏结构 + OI未崩 + 卖压衰减 + 主动资金重新增强 + 二次突破局部高低

强确认（§15.5）：5m 突破确认 + 15m 同向确认 + 1h 不逆向。

每个 symbol 维护独立突破状态机。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domain import KlineEvent


@dataclass
class BreakoutState:
    """单个 symbol 的突破生命周期状态。"""

    symbol: str
    # 阶段 1：突破
    breakout_level: float | None = None
    breakout_time: int | None = None
    breakout_price: float | None = None
    breakout_direction: str | None = None  # up / down
    breakout_confirmed: bool = False  # 5m 收盘站外
    # 阶段 2：保持
    breakout_hold: bool = False
    time_above_level_ms: int = 0
    max_retrace: float = 0.0  # 突破后最大回撤比例
    close_back_inside: bool = False  # 收盘回到区间内
    # 阶段 3：回踩
    retest_started: bool = False
    retest_low: float | None = None
    retest_high: float | None = None
    retest_depth: float | None = None
    retest_volume: float | None = None
    retest_delta: float | None = None
    retest_oi: float | None = None
    # 阶段 4：二次确认
    retest_confirmed: bool = False
    # 强确认
    strong_confirm: bool = False
    # 最后更新
    last_update_ms: int | None = None


@dataclass
class BreakoutLifecycleResult:
    """突破生命周期判定结果。"""

    breakout_level: float | None
    breakout_time: int | None
    breakout_direction: str | None
    breakout_confirmed: bool
    breakout_hold: bool
    time_above_level_ms: int
    max_retrace: float
    close_back_inside: bool
    retest_started: bool
    retest_depth: float | None
    retest_confirmed: bool
    strong_confirm: bool
    confirmation_strength: str  # strong / medium / weak / none
    label: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "breakout_level": self.breakout_level,
            "breakout_time": self.breakout_time,
            "breakout_direction": self.breakout_direction,
            "breakout_confirmed": self.breakout_confirmed,
            "breakout_hold": self.breakout_hold,
            "time_above_level_ms": self.time_above_level_ms,
            "max_retrace": self.max_retrace,
            "close_back_inside": self.close_back_inside,
            "retest_started": self.retest_started,
            "retest_depth": self.retest_depth,
            "retest_confirmed": self.retest_confirmed,
            "strong_confirm": self.strong_confirm,
            "confirmation_strength": self.confirmation_strength,
            "label": self.label,
        }


class BreakoutLifecycleEngine:
    """突破生命周期引擎。"""

    def __init__(
        self,
        retest_depth_max: float = 0.5,
        retest_healthy_max: float = 0.382,
        min_hold_ms: int = 60_000,
    ) -> None:
        self.retest_depth_max = retest_depth_max
        self.retest_healthy_max = retest_healthy_max
        self.min_hold_ms = min_hold_ms
        self._states: dict[str, BreakoutState] = {}

    def get_state(self, symbol: str) -> BreakoutState:
        if symbol not in self._states:
            self._states[symbol] = BreakoutState(symbol=symbol)
        return self._states[symbol]

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._states.clear()
        else:
            self._states.pop(symbol, None)

    def update(
        self,
        symbol: str,
        now_ms: int,
        *,
        breakout_level: float | None = None,
        current_price: float | None = None,
        kline_5m: KlineEvent | None = None,
        context_15m: float | None = None,
        context_1h: float | None = None,
        fv: dict[str, Any] | None = None,
    ) -> BreakoutLifecycleResult:
        """更新突破生命周期。

        Args:
            breakout_level: 关键突破位（来自 Structure Engine，P13）。None 表示尚未注入。
            current_price: 当前价。
            kline_5m: 最新 5m closed bar（用于判断收盘站外）。
            context_15m / context_1h: 15m/1h 周期 return（强确认用）。
            fv: feature dict（acceptance/retrace_ratio/oi/delta 等）。
        """
        st = self.get_state(symbol)
        st.last_update_ms = now_ms
        fv = fv or {}

        # ── 阶段 1：突破确认（5m 收盘站外）──
        if breakout_level is not None:
            self._check_breakout(st, breakout_level, kline_5m, now_ms)

        # ── 阶段 2：保持 ──
        if st.breakout_confirmed and current_price is not None and st.breakout_level is not None:
            self._check_hold(st, current_price, now_ms, fv)

        # ── 阶段 3：回踩 ──
        if st.breakout_confirmed and current_price is not None and st.breakout_level is not None:
            self._check_retest(st, current_price, now_ms, fv)

        # ── 阶段 4：二次确认 ──
        if st.retest_started:
            self._check_second_confirmation(st, fv, now_ms)

        # ── 强确认（§15.5）──
        st.strong_confirm = self._check_strong(st, context_15m, context_1h, fv)

        # 文案
        strength, label = self._label(st)
        factors = {
            "breakout_level": st.breakout_level,
            "breakout_confirmed": st.breakout_confirmed,
            "breakout_hold": st.breakout_hold,
            "retest_started": st.retest_started,
            "retest_confirmed": st.retest_confirmed,
            "strong_confirm": st.strong_confirm,
            "max_retrace": st.max_retrace,
        }
        return BreakoutLifecycleResult(
            breakout_level=st.breakout_level,
            breakout_time=st.breakout_time,
            breakout_direction=st.breakout_direction,
            breakout_confirmed=st.breakout_confirmed,
            breakout_hold=st.breakout_hold,
            time_above_level_ms=st.time_above_level_ms,
            max_retrace=st.max_retrace,
            close_back_inside=st.close_back_inside,
            retest_started=st.retest_started,
            retest_depth=st.retest_depth,
            retest_confirmed=st.retest_confirmed,
            strong_confirm=st.strong_confirm,
            confirmation_strength=strength,
            label=label,
            factors=factors,
        )

    def _check_breakout(self, st: BreakoutState, level: float, kline_5m: KlineEvent | None, now_ms: int) -> None:
        """5m 收盘 K 线站在关键位置外才算正式突破。"""
        if kline_5m is None or not kline_5m.is_closed:
            # 未收盘不确认突破（§15.1：不能只因为最高价刺穿）
            return
        close = float(kline_5m.close)
        high = float(kline_5m.high)
        low = float(kline_5m.low)
        # 向上突破：收盘价 > level
        if close > level:
            if not st.breakout_confirmed or st.breakout_direction != "up":
                st.breakout_level = level
                st.breakout_time = kline_5m.open_time
                st.breakout_price = close
                st.breakout_direction = "up"
                st.breakout_confirmed = True
                st.time_above_level_ms = 0
                st.max_retrace = 0.0
                st.close_back_inside = False
                st.retest_started = False
                st.retest_confirmed = False
        # 向下突破：收盘价 < level
        elif close < level:
            if not st.breakout_confirmed or st.breakout_direction != "down":
                st.breakout_level = level
                st.breakout_time = kline_5m.open_time
                st.breakout_price = close
                st.breakout_direction = "down"
                st.breakout_confirmed = True
                st.time_above_level_ms = 0
                st.max_retrace = 0.0
                st.close_back_inside = False
                st.retest_started = False
                st.retest_confirmed = False

    def _check_hold(self, st: BreakoutState, current_price: float, now_ms: int, fv: dict[str, Any]) -> None:
        """突破保持：价格在突破位正确侧的时间 + 回撤控制。"""
        level = st.breakout_level or 0.0
        if st.breakout_direction == "up":
            above = current_price > level
            # 回撤比例（相对突破幅度）
            if st.breakout_price and st.breakout_price > level:
                move = st.breakout_price - level
                retrace = (st.breakout_price - current_price) / move if move > 0 else 0.0
                st.max_retrace = max(st.max_retrace, max(0.0, retrace))
        elif st.breakout_direction == "down":
            above = current_price < level
            if st.breakout_price and st.breakout_price < level:
                move = level - st.breakout_price
                retrace = (current_price - st.breakout_price) / move if move > 0 else 0.0
                st.max_retrace = max(st.max_retrace, max(0.0, retrace))
        else:
            above = True

        # 收盘回到区间内
        st.close_back_inside = not above
        # 保持 = 未收盘回到区间内 + 回撤可控
        st.breakout_hold = above and st.max_retrace < self.retest_depth_max

        # 累计在线时间
        if st.breakout_time is not None and above:
            st.time_above_level_ms = now_ms - st.breakout_time

    def _check_retest(self, st: BreakoutState, current_price: float, now_ms: int, fv: dict[str, Any]) -> None:
        """回踩：价格回到突破位附近但未破坏。"""
        level = st.breakout_level or 0.0
        if st.breakout_direction == "up":
            # 回踩 = 价格从突破侧回落接近 level
            if not st.retest_started and current_price < (st.breakout_price or level) * (1 - 0.005):
                st.retest_started = True
            if st.retest_started:
                st.retest_low = min(st.retest_low or current_price, current_price)
                if st.breakout_price and st.breakout_price > level:
                    move = st.breakout_price - level
                    st.retest_depth = (st.breakout_price - current_price) / move if move > 0 else 0.0
        elif st.breakout_direction == "down":
            if not st.retest_started and current_price > (st.breakout_price or level) * (1 + 0.005):
                st.retest_started = True
            if st.retest_started:
                st.retest_high = max(st.retest_high or current_price, current_price)
                if st.breakout_price and st.breakout_price < level:
                    move = level - st.breakout_price
                    st.retest_depth = (current_price - st.breakout_price) / move if move > 0 else 0.0

        if st.retest_started:
            st.retest_volume = fv.get("volume_5m", st.retest_volume)
            st.retest_delta = fv.get("signed_delta") or fv.get("taker_delta") or st.retest_delta
            st.retest_oi = fv.get("oi_change_5m", st.retest_oi)

    def _check_second_confirmation(self, st: BreakoutState, fv: dict[str, Any], now_ms: int) -> None:
        """二次确认：回踩没破坏结构 + OI未崩 + 卖压衰减 + 主动资金重新增强。"""
        # 回踩深度健康
        depth_ok = st.retest_depth is not None and st.retest_depth <= self.retest_healthy_max
        # OI 未崩
        oi_ok = st.retest_oi is None or st.retest_oi > -0.02
        # 卖压衰减（acceptance 恢复 或 retrace 不再扩大）
        accept = fv.get("acceptance")
        sell_decay = accept is not None and accept > 0.5
        # 主动资金重新增强（delta 方向恢复）
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        cvd_z = fv.get("cvd_slope_z")
        sign = 1.0 if st.breakout_direction == "up" else (-1.0 if st.breakout_direction == "down" else 0.0)
        flow_reignite = False
        if delta is not None and sign != 0:
            flow_reignite = delta * sign > 0
        if cvd_z is not None and sign != 0:
            flow_reignite = flow_reignite or (cvd_z * sign > 0)

        st.retest_confirmed = depth_ok and oi_ok and (sell_decay or flow_reignite)

    def _check_strong(self, st: BreakoutState, context_15m: float | None, context_1h: float | None, fv: dict[str, Any]) -> bool:
        """强确认：5m 突破确认 + 15m 同向 + 1h 不逆向。"""
        if not st.breakout_confirmed or not st.breakout_hold:
            return False
        sign = 1.0 if st.breakout_direction == "up" else (-1.0 if st.breakout_direction == "down" else 0.0)
        if sign == 0:
            return False
        # 15m 同向
        tf_15m_ok = context_15m is not None and context_15m * sign > 0
        # 1h 不逆向（同向或中性）
        tf_1h_ok = context_1h is None or context_1h * sign >= 0
        return tf_15m_ok and tf_1h_ok

    def _label(self, st: BreakoutState) -> tuple[str, str]:
        if st.strong_confirm:
            return "strong", "5m 突破确认 + 15m 同向 + 1h 不逆向"
        if st.retest_confirmed:
            return "medium", "突破后回踩健康，二次确认通过"
        if st.retest_started:
            return "weak", "突破后正在回踩，等待二次确认"
        if st.breakout_hold:
            return "medium", "突破保持中"
        if st.breakout_confirmed:
            return "weak", "突破已确认，观察保持"
        return "none", "尚未突破"
