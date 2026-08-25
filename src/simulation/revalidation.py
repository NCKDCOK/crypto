"""Entry Revalidation — §26 入场二次验证（十项检查）。

依据：V1.3 更新计划 §25 / §26 / §27 / §28。

- §25：价格进入 reference_entry_low~high 后必须 REVALIDATING，不能直接成交。
- §26：至少 10 项检查（数据健康 / State 合法 / Setup 未失效 / Breakout 未失效 /
  Withdrawal 未触发 / Direction 未翻转 / OI 未严重反向 / CVD 未严重反转 /
  Spot-Perp 未恶化 / Regime 未重反转 / Pump Risk 未升高）。
- §27：全部通过 → ARMED；任一不通过 → CANCELLED 并记录原因。
- §28：第一版模拟成交 = Entry Zone 内第一笔符合 Revalidation 的价格。

ctx 约定（由 runtime 构建，见 runtime._build_simulation_ctx）：
    price, state, setup_type, direction,
    confidence_state, data_confidence, data_age_ms,
    features（fv_dict：key → float | None）,
    breakout / structure / spot_perp / regime（to_dict 结果）,
    pump_risk, distribution_risk, withdrawal_active, invalidated
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.simulation.snapshot import FORMAL_STATES, INVALID_SETUPS

# §18 正式状态集合（此处为字符串版，供 ctx.state 比较）


@dataclass
class RevalidationCheck:
    """单项检查结果。"""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class RevalidationResult:
    """二次验证汇总。"""

    passed: bool
    checks: list[RevalidationCheck] = field(default_factory=list)
    fail_reason: str | None = None

    @property
    def passed_checks(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "passed_checks": self.passed_checks,
            "total_checks": len(self.checks),
            "fail_reason": self.fail_reason,
            "checks": [c.to_dict() for c in self.checks],
        }


class EntryRevalidationEngine:
    """入场二次验证引擎。阈值全部配置化（runtime 从 SimulationConfig/RankingConfig 注入）。"""

    def __init__(
        self,
        *,
        stale_max_s: float = 30.0,
        min_data_confidence: float = 85.0,
        oi_reversal_threshold: float = -0.05,
        cvd_reversal_z: float = 2.0,
        spot_agreement_bad: float = -0.3,
        max_pump_risk: float = 50.0,
        risk_off_regimes: tuple[str, ...] = ("DELEVERAGING", "PANIC", "CHOP"),
    ) -> None:
        self.stale_max_ms = int(stale_max_s * 1000.0)
        self.min_data_confidence = min_data_confidence
        self.oi_reversal_threshold = oi_reversal_threshold
        self.cvd_reversal_z = cvd_reversal_z
        self.spot_agreement_bad = spot_agreement_bad
        self.max_pump_risk = max_pump_risk
        self.risk_off_regimes = set(risk_off_regimes)

    # ── 十项检查 ──

    def _check_data_health(self, ctx: dict[str, Any]) -> RevalidationCheck:
        age = ctx.get("data_age_ms")
        if age is not None and age > self.stale_max_ms:
            return RevalidationCheck("data_health", False,
                                     f"数据超过 {self.stale_max_ms // 1000}s 未更新（stale {age}ms）")
        if ctx.get("confidence_state") == "UNKNOWN":
            return RevalidationCheck("data_health", False, "关键数据流 STALE/FAIL，置信度 UNKNOWN")
        dc = ctx.get("data_confidence")
        if dc is None or dc < self.min_data_confidence:
            return RevalidationCheck(
                "data_health", False,
                f"data_confidence {dc} < {self.min_data_confidence}（§26 数据健康）")
        return RevalidationCheck("data_health", True, "数据健康达标")

    def _check_state(self, ctx: dict[str, Any]) -> RevalidationCheck:
        state = ctx.get("state")
        if state not in FORMAL_STATES:
            return RevalidationCheck(
                "state_legal", False, f"状态 {state} 不在正式范围（§26 检查 2）")
        return RevalidationCheck("state_legal", True, "State 合法")

    def _check_setup(self, ctx: dict[str, Any], snapshot: dict[str, Any]) -> RevalidationCheck:
        cur = ctx.get("setup_type")
        # 进入失效集合（含 NONE）视为 Setup 失效；方向性 Setup 家族内互转
        # （如 TREND_CONTINUATION ↔ RETEST_REIGNITION）不算失效，方向变化由检查 6 负责。
        if cur in INVALID_SETUPS:
            return RevalidationCheck(
                "setup_alive", False, f"Setup 已失效：{cur}（§26 检查 3）")
        return RevalidationCheck("setup_alive", True, "Setup 未失效")

    def _check_breakout(self, ctx: dict[str, Any]) -> RevalidationCheck:
        b = ctx.get("breakout") or {}
        if b.get("close_back_inside"):
            return RevalidationCheck(
                "breakout_alive", False, "收盘回到突破区间内，突破结构失效（§26 检查 4）")
        s = ctx.get("structure") or {}
        if s.get("failed_breakout"):
            return RevalidationCheck("breakout_alive", False, "Failed Breakout（§26 检查 4）")
        if s.get("failed_breakdown"):
            return RevalidationCheck("breakout_alive", False, "Failed Breakdown（§26 检查 4）")
        return RevalidationCheck("breakout_alive", True, "Breakout / 结构未失效")

    def _check_withdrawal(self, ctx: dict[str, Any]) -> RevalidationCheck:
        if ctx.get("withdrawal_active"):
            return RevalidationCheck("withdrawal", False, "Withdrawal 已触发（§26 检查 5）")
        return RevalidationCheck("withdrawal", True, "无撤离信号")

    def _check_direction(self, ctx: dict[str, Any], snapshot: dict[str, Any]) -> RevalidationCheck:
        cur = ctx.get("direction")
        snap_dir = snapshot.get("direction")
        if cur in (None, "NEUTRAL"):
            return RevalidationCheck("direction_stable", False, f"方向丢失/中性（{cur}）（§26 检查 6）")
        if snap_dir and cur != snap_dir:
            return RevalidationCheck(
                "direction_stable", False, f"方向翻转 {snap_dir}→{cur}（§26 检查 6）")
        return RevalidationCheck("direction_stable", True, "方向未翻转")

    def _check_oi(self, ctx: dict[str, Any], direction: str) -> RevalidationCheck:
        oi = (ctx.get("features") or {}).get("oi_change_5m")
        if oi is None:
            return RevalidationCheck("oi", True, "OI 数据缺失（保守放行，不阻塞）")
        if direction == "LONG" and oi < self.oi_reversal_threshold:
            return RevalidationCheck(
                "oi", False, f"OI 严重反向收缩 {oi:.3f}（§26 检查 7）")
        if direction == "SHORT" and oi > -self.oi_reversal_threshold:
            return RevalidationCheck(
                "oi", False, f"OI 严重反向扩张 {oi:.3f}（§26 检查 7）")
        return RevalidationCheck("oi", True, "OI 方向正常")

    def _check_cvd(self, ctx: dict[str, Any], direction: str) -> RevalidationCheck:
        z = (ctx.get("features") or {}).get("cvd_slope_z")
        if z is None:
            return RevalidationCheck("cvd", True, "CVD 数据缺失（保守放行）")
        if direction == "LONG" and z < -self.cvd_reversal_z:
            return RevalidationCheck(
                "cvd", False, f"CVD 严重反转 z={z:.2f}（§26 检查 8）")
        if direction == "SHORT" and z > self.cvd_reversal_z:
            return RevalidationCheck(
                "cvd", False, f"CVD 严重反转 z={z:.2f}（§26 检查 8）")
        return RevalidationCheck("cvd", True, "CVD 方向正常")

    def _check_spot_perp(self, ctx: dict[str, Any], direction: str) -> RevalidationCheck:
        sp = ctx.get("spot_perp") or {}
        if sp.get("classification") == "leverage_dominant":
            return RevalidationCheck(
                "spot_perp", False, "现货未确认、合约杠杆主导（§26 检查 9）")
        agreement = (ctx.get("features") or {}).get("spot_perp_agreement")
        if direction == "LONG" and agreement is not None and agreement < self.spot_agreement_bad:
            return RevalidationCheck(
                "spot_perp", False, f"现货合约一致性恶化 {agreement:.2f}（§26 检查 9）")
        return RevalidationCheck("spot_perp", True, "Spot-Perp 未恶化")

    def _check_regime(self, ctx: dict[str, Any]) -> RevalidationCheck:
        rg = ctx.get("regime") or {}
        regime = rg.get("regime")
        if regime in self.risk_off_regimes:
            return RevalidationCheck(
                "regime_stable", False, f"市场 regime 转 {regime}（§26 检查 10）")
        return RevalidationCheck("regime_stable", True, "Regime 未重反转")

    def _check_pump(self, ctx: dict[str, Any]) -> RevalidationCheck:
        pump = ctx.get("pump_risk")
        if pump is not None and pump > self.max_pump_risk:
            return RevalidationCheck(
                "pump_risk", False, f"Pump Risk 升高 {pump:.1f}（§26 检查 11）")
        return RevalidationCheck("pump_risk", True, "Pump Risk 正常")

    # ── 主入口 ──

    def evaluate(self, ctx: dict[str, Any], snapshot: dict[str, Any], now_ms: int) -> RevalidationResult:
        """§26 十项检查。全部通过 → passed=True；否则 fail_reason=首个失败项。"""
        direction = ctx.get("direction")
        checks: list[RevalidationCheck] = [
            self._check_data_health(ctx),
            self._check_state(ctx),
            self._check_setup(ctx, snapshot),
            self._check_breakout(ctx),
            self._check_withdrawal(ctx),
            self._check_direction(ctx, snapshot),
            self._check_oi(ctx, direction),
            self._check_cvd(ctx, direction),
            self._check_spot_perp(ctx, direction),
            self._check_regime(ctx),
            self._check_pump(ctx),
        ]
        for c in checks:
            if not c.passed:
                return RevalidationResult(passed=False, checks=checks, fail_reason=c.detail)
        return RevalidationResult(passed=True, checks=checks)