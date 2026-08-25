"""Score Engine — 可解释评分引擎。

依据：V1.1 计划 §十三~§十六
- 10 个子评分，每个 0~100
- OpportunityScore = 加权基础分 - 风险扣分
- 权重全部来自配置，禁止 magic number
- 每个分数输出 breakdown（components + evidence）
- 评分预热：样本不足时不评分
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.config import ScoringConfig
from src.domain import FeatureSnapshot, State

logger = logging.getLogger(__name__)


@dataclass
class ScoreComponent:
    """单个评分组件。"""

    name: str
    value: float | None
    contribution: float  # 对子评分的贡献（0~100 缩放后）
    description: str = ""


@dataclass
class SubScore:
    """子评分结果。"""

    name: str
    label: str
    score: float  # 0~100
    available: bool
    components: list[ScoreComponent] = field(default_factory=list)
    is_risk: bool = False  # 风险分类子评分


@dataclass
class ScoreBreakdown:
    """完整评分结果。"""

    opportunity_score: float  # 0~100
    available: bool
    subscores: dict[str, SubScore] = field(default_factory=dict)
    risk_penalty: float = 0.0
    base_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_score": round(self.opportunity_score, 1) if self.available else None,
            "available": self.available,
            "base_score": round(self.base_score, 1),
            "risk_penalty": round(self.risk_penalty, 1),
            "subscores": {
                k: {
                    "label": v.label,
                    "score": round(v.score, 1),
                    "available": v.available,
                    "is_risk": v.is_risk,
                    "components": [
                        {"name": c.name, "value": c.value, "contribution": round(c.contribution, 1)}
                        for c in v.components
                    ],
                }
                for k, v in self.subscores.items()
            },
        }


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _sigmoid_z(z: float | None, scale: float = 15.0) -> float:
    """将 z-score 映射到 0~100（sigmoid，z=0 → 50, z=3 → ~95）。"""
    if z is None:
        return 50.0
    import math
    # 限制指数参数范围，防止 math.exp 溢出
    exp_arg = max(-700.0, min(700.0, -z * scale / 30.0))
    return _clamp(100.0 / (1.0 + math.exp(exp_arg)))


def _dir_sign(direction: str | None) -> float:
    """方向符号：LONG → +1, SHORT → -1, None → 0。"""
    if direction == "LONG":
        return 1.0
    if direction == "SHORT":
        return -1.0
    return 0.0


def _aligned_score(value: float | None, direction: str | None, scale: float = 15.0) -> float:
    """方向对齐评分：value 与 direction 同向 → 高分。"""
    if value is None:
        return 50.0
    sign = _dir_sign(direction)
    if sign == 0:
        return 50.0
    aligned = value * sign
    return _sigmoid_z(aligned, scale)


class ScoreEngine:
    """可解释评分引擎。

    从 FeatureSnapshot + State 计算 10 个子评分 + OpportunityScore。
    """

    def __init__(self, cfg: ScoringConfig) -> None:
        self.cfg = cfg

    def compute(
        self,
        snap: FeatureSnapshot,
        state: State,
        direction: str | None,
        evidence_count: int,
        state_since_ms: int | None,
        now_ms: int,
        sample_count: int = 0,
    ) -> ScoreBreakdown:
        """计算完整评分。"""
        w = self.cfg

        # 评分预热检查
        available = sample_count >= w.warmup_min_samples
        if not available:
            return ScoreBreakdown(opportunity_score=0.0, available=False)

        feats = snap.features
        fv = {k: (v.value if v.available else None) for k, v in feats.items()}

        # ── 基础机会分子评分 ──
        capital = self._capital_inflow(fv, direction)
        startup = self._startup_quality(fv, direction)
        trend = self._trend(fv, direction)
        stamina = self._immediate_stamina(fv, direction)
        sustained = self._sustained_startup(fv, direction, state, evidence_count, state_since_ms, now_ms)
        anomaly = self._anomaly_intensity(fv)
        chase = self._chase_safety(fv, direction)

        # ── 风险子评分 ──
        top_risk = self._top_risk(fv, direction)
        crowding = self._crowding_risk(fv)
        withdrawal = self._withdrawal_risk(fv, direction)
        chase_risk = self._chase_risk(fv, direction)

        subscores = {
            "capital_inflow": capital,
            "startup_quality": startup,
            "trend": trend,
            "immediate_stamina": stamina,
            "sustained_startup": sustained,
            "anomaly_intensity": anomaly,
            "chase_safety": chase,
            "top_risk": top_risk,
            "crowding_risk": crowding,
            "withdrawal_risk": withdrawal,
            "chase_risk": chase_risk,
        }

        # ── 基础机会分（加权）──
        base = (
            capital.score * w.w_capital_inflow
            + startup.score * w.w_startup_quality
            + trend.score * w.w_trend
            + stamina.score * w.w_immediate_stamina
            + sustained.score * w.w_sustained_startup
            + anomaly.score * w.w_anomaly_intensity
            + chase.score * w.w_chase_safety
        )

        # ── 风险扣分 ──
        risk_raw = (
            top_risk.score * w.w_top_risk
            + crowding.score * w.w_crowding_risk
            + withdrawal.score * w.w_withdrawal_risk
            + chase_risk.score * w.w_chase_risk
        )
        risk_penalty = risk_raw * w.risk_penalty_scale

        opportunity = _clamp(base - risk_penalty)

        return ScoreBreakdown(
            opportunity_score=opportunity,
            available=True,
            subscores=subscores,
            risk_penalty=risk_penalty,
            base_score=base,
        )

    # ── 基础机会分子评分 ──

    def _capital_inflow(self, fv: dict, direction: str | None) -> SubScore:
        """资金输入：新增方向资金是否真的进入？"""
        oi_exp = fv.get("oi_change_5m")
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        cvd_z = fv.get("cvd_slope_z")
        cvd_accel = fv.get("cvd_accel_z")
        price_resp = fv.get("price_return_30s")

        comps = [
            ScoreComponent("oi_expansion_5m", oi_exp, _aligned_score(oi_exp, direction) * 0.25, "OI 5m 扩张"),
            ScoreComponent("taker_delta", delta, _aligned_score(delta, direction, 0.001) * 0.25, "主动买卖差"),
            ScoreComponent("cvd_slope_z", cvd_z, _aligned_score(cvd_z, direction) * 0.25, "CVD 斜率 Z"),
            ScoreComponent("cvd_accel_z", cvd_accel, _aligned_score(cvd_accel, direction) * 0.15, "CVD 加速 Z"),
            ScoreComponent("price_response", price_resp, _aligned_score(price_resp, direction, 50) * 0.10, "价格响应"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("capital_inflow", "资金输入", score, True, comps)

    def _startup_quality(self, fv: dict, direction: str | None) -> SubScore:
        """启动质量：这次异动是不是像真正启动？"""
        vol_z = fv.get("volume_z")
        tc_z = fv.get("trade_count_z")
        price_accel = fv.get("price_acceleration")
        oi_1m = fv.get("oi_change_1m")
        cvd_z = fv.get("cvd_slope_z")
        accept = fv.get("acceptance")

        comps = [
            ScoreComponent("volume_z", vol_z, _sigmoid_z(vol_z) * 0.20, "成交量 Z"),
            ScoreComponent("trade_count_z", tc_z, _sigmoid_z(tc_z) * 0.15, "成交笔数 Z"),
            ScoreComponent("price_acceleration", price_accel, _aligned_score(price_accel, direction, 100) * 0.20, "价格加速度"),
            ScoreComponent("oi_confirmation_1m", oi_1m, _aligned_score(oi_1m, direction) * 0.15, "OI 1m 确认"),
            ScoreComponent("flow_confirmation", cvd_z, _aligned_score(cvd_z, direction) * 0.15, "资金流确认"),
            ScoreComponent("breakout_acceptance", accept, _sigmoid_z((accept or 0) * 100) * 0.15, "突破有效性"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("startup_quality", "启动质量", score, True, comps)

    def _trend(self, fv: dict, direction: str | None) -> SubScore:
        """趋势：当前方向是否稳定？基于多周期 Kline context。"""
        intervals = ["1m", "5m", "15m", "1h"]
        weights = [0.15, 0.25, 0.30, 0.30]
        comps = []
        total = 0.0
        for iv, wt in zip(intervals, weights):
            val = fv.get(f"context_{iv}")
            contrib = _aligned_score(val, direction, 200) * wt
            comps.append(ScoreComponent(f"context_{iv}", val, contrib, f"{iv} 周期趋势"))
            total += contrib
        score = _clamp(total)
        return SubScore("trend", "趋势", score, True, comps)

    def _immediate_stamina(self, fv: dict, direction: str | None) -> SubScore:
        """即时续航：最近几十秒到几分钟，资金还在不在？"""
        delta_1m = fv.get("taker_delta_1m")
        cvd_slope = fv.get("CVD_slope")
        oi_30s = fv.get("oi_change_30s")
        eff = fv.get("price_efficiency")
        retrace = fv.get("retrace_ratio")

        retrace_score = _clamp(100.0 - (retrace or 0) * 100.0) if retrace is not None else 50.0

        comps = [
            ScoreComponent("delta_persistence_1m", delta_1m, _aligned_score(delta_1m, direction, 0.001) * 0.25, "Delta 持续性"),
            ScoreComponent("cvd_persistence", cvd_slope, _aligned_score(cvd_slope, direction, 0.001) * 0.25, "CVD 持续性"),
            ScoreComponent("oi_persistence_30s", oi_30s, _aligned_score(oi_30s, direction) * 0.20, "OI 持续性"),
            ScoreComponent("price_efficiency", eff, _sigmoid_z((eff or 0) * 100 - 50) * 0.15, "价格效率"),
            ScoreComponent("retrace_quality", retrace, retrace_score * 0.15, "回踩质量"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("immediate_stamina", "即时续航", score, True, comps)

    def _sustained_startup(
        self, fv: dict, direction: str | None, state: State,
        evidence_count: int, state_since_ms: int | None, now_ms: int,
    ) -> SubScore:
        """持续启动：启动是不是只有一波，还是有持续性？"""
        # 状态加权
        state_weights = {
            State.START_CONFIRMED: 80.0,
            State.CONTINUATION: 90.0,
            State.SUSPECTED_START: 50.0,
            State.ANOMALY: 30.0,
            State.EXHAUSTION: 20.0,
            State.WITHDRAWAL: 10.0,
        }
        state_score = state_weights.get(state, 40.0)

        # 持续时间加分（最多 60s → 满分）
        duration_s = 0.0
        if state_since_ms is not None:
            duration_s = (now_ms - state_since_ms) / 1000.0
        duration_score = _clamp(min(duration_s / 60.0, 1.0) * 100.0)

        # 证据数加分
        evidence_score = _clamp(min(evidence_count / 5.0, 1.0) * 100.0)

        comps = [
            ScoreComponent("state_quality", state.value, state_score * 0.40, "状态质量"),
            ScoreComponent("duration", duration_s, duration_score * 0.30, "持续时间"),
            ScoreComponent("evidence_count", float(evidence_count), evidence_score * 0.30, "证据数量"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("sustained_startup", "持续启动", score, True, comps)

    def _anomaly_intensity(self, fv: dict) -> SubScore:
        """异动强度：当前变化相对历史是否异常？"""
        vol_z = fv.get("volume_z")
        tc_z = fv.get("trade_count_z")
        price_accel = fv.get("price_acceleration")
        oi_5m = fv.get("oi_change_5m")

        comps = [
            ScoreComponent("volume_z", vol_z, _sigmoid_z(vol_z) * 0.30, "成交量 Z"),
            ScoreComponent("trade_count_z", tc_z, _sigmoid_z(tc_z) * 0.25, "成交笔数 Z"),
            ScoreComponent("price_acceleration", price_accel, _sigmoid_z(price_accel) * 0.25, "价格加速度 Z"),
            ScoreComponent("oi_delta_5m", oi_5m, _sigmoid_z((oi_5m or 0) * 100) * 0.20, "OI 变化 Z"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("anomaly_intensity", "异动强度", score, True, comps)

    def _chase_safety(self, fv: dict, direction: str | None) -> SubScore:
        """追涨安全：现在是不是已经太晚？"""
        price_5m = fv.get("price_return_5m")
        retrace = fv.get("retrace_ratio")
        eff = fv.get("price_efficiency")
        funding_pct = fv.get("funding_percentile")
        context_5m = fv.get("context_5m")

        # 涨幅过大 → 追涨不安全
        ext_score = 50.0
        if price_5m is not None:
            ext_score = _clamp(100.0 - abs(price_5m) * 10.0)

        retrace_score = _clamp(100.0 - (retrace or 0) * 100.0) if retrace is not None else 50.0
        eff_score = _sigmoid_z((eff or 0) * 100 - 50)
        crowd_score = _clamp(100.0 - (funding_pct or 50))
        local_ext = _clamp(100.0 - abs(context_5m or 0) * 10.0) if context_5m is not None else 50.0

        comps = [
            ScoreComponent("distance_from_initiation", price_5m, ext_score * 0.30, "距启动幅度"),
            ScoreComponent("retrace", retrace, retrace_score * 0.20, "回撤幅度"),
            ScoreComponent("efficiency", eff, eff_score * 0.20, "价格效率"),
            ScoreComponent("crowding", funding_pct, crowd_score * 0.15, "拥挤度"),
            ScoreComponent("local_extension", context_5m, local_ext * 0.15, "局部延伸"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("chase_safety", "追涨安全", score, True, comps)

    # ── 风险子评分 ──

    def _top_risk(self, fv: dict, direction: str | None) -> SubScore:
        """顶部风险：是否出现衰竭迹象？"""
        cvd_z = fv.get("cvd_slope_z")
        vol_z = fv.get("volume_z")
        eff = fv.get("price_efficiency")
        oi_5m = fv.get("oi_change_5m")
        accept = fv.get("acceptance")

        # CVD 背离：方向向上但 CVD 向下
        divergence = 0.0
        if cvd_z is not None and direction:
            sign = _dir_sign(direction)
            if cvd_z * sign < 0:
                divergence = _clamp(abs(cvd_z) * 20.0)

        # 高量低效
        high_vol_low_eff = 0.0
        if vol_z is not None and vol_z > 2 and eff is not None and eff < 0.3:
            high_vol_low_eff = _clamp((vol_z - 2) * 20.0)

        oi_decay = _clamp(abs(min(oi_5m or 0, 0)) * 500.0)
        eff_collapse = _clamp((1.0 - (eff or 0)) * 100.0) if eff is not None else 0.0
        failed_breakout = _clamp((1.0 - (accept or 0)) * 100.0) if accept is not None else 0.0

        comps = [
            ScoreComponent("cvd_divergence", cvd_z, divergence * 0.30, "CVD 背离"),
            ScoreComponent("high_vol_low_eff", vol_z, high_vol_low_eff * 0.25, "放量滞涨"),
            ScoreComponent("oi_decay", oi_5m, oi_decay * 0.20, "OI 衰减"),
            ScoreComponent("efficiency_collapse", eff, eff_collapse * 0.15, "效率坍塌"),
            ScoreComponent("failed_breakout", accept, failed_breakout * 0.10, "突破失败"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("top_risk", "顶部风险", score, True, comps, is_risk=True)

    def _crowding_risk(self, fv: dict) -> SubScore:
        """拥挤风险：Funding / Premium / 情绪过热。"""
        funding = fv.get("funding")
        funding_pct = fv.get("funding_percentile")
        premium_pct = fv.get("premium_percentile")

        funding_score = _clamp(abs(funding or 0) * 5000.0)  # 0.02 → 100
        fp_score = _clamp(funding_pct or 0)
        pp_score = _clamp(premium_pct or 0)

        comps = [
            ScoreComponent("funding_rate", funding, funding_score * 0.40, "资金费率"),
            ScoreComponent("funding_percentile", funding_pct, fp_score * 0.35, "费率分位"),
            ScoreComponent("premium_percentile", premium_pct, pp_score * 0.25, "溢价分位"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("crowding_risk", "拥挤风险", score, True, comps, is_risk=True)

    def _withdrawal_risk(self, fv: dict, direction: str | None) -> SubScore:
        """撤离风险：OI 衰减 / Delta 反转 / CVD 反转 / 效率坍塌。"""
        oi_5m = fv.get("oi_change_5m")
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        cvd_z = fv.get("cvd_slope_z")
        eff = fv.get("price_efficiency")
        accept = fv.get("acceptance")

        oi_decay = _clamp(abs(min(oi_5m or 0, 0)) * 500.0)

        # Delta 反转
        delta_rev = 0.0
        if delta is not None and direction:
            sign = _dir_sign(direction)
            if delta * sign < 0:
                delta_rev = _clamp(abs(delta) * 0.01)

        # CVD 反转
        cvd_rev = 0.0
        if cvd_z is not None and direction:
            sign = _dir_sign(direction)
            if cvd_z * sign < 0:
                cvd_rev = _clamp(abs(cvd_z) * 20.0)

        eff_collapse = _clamp((1.0 - (eff or 0)) * 100.0) if eff is not None else 0.0
        failed_accept = _clamp((1.0 - (accept or 0)) * 100.0) if accept is not None else 0.0

        comps = [
            ScoreComponent("oi_decay", oi_5m, oi_decay * 0.30, "OI 衰减"),
            ScoreComponent("delta_reversal", delta, delta_rev * 0.25, "Delta 反转"),
            ScoreComponent("cvd_reversal", cvd_z, cvd_rev * 0.20, "CVD 反转"),
            ScoreComponent("efficiency_collapse", eff, eff_collapse * 0.15, "效率坍塌"),
            ScoreComponent("failed_acceptance", accept, failed_accept * 0.10, "突破未站稳"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("withdrawal_risk", "撤离风险", score, True, comps, is_risk=True)

    def _chase_risk(self, fv: dict, direction: str | None) -> SubScore:
        """追涨风险：追涨安全的风险侧（高分 = 风险大）。"""
        price_5m = fv.get("price_return_5m")
        retrace = fv.get("retrace_ratio")
        context_5m = fv.get("context_5m")

        ext_risk = _clamp(abs(price_5m or 0) * 10.0)
        retrace_risk = _clamp((retrace or 0) * 100.0)
        local_risk = _clamp(abs(context_5m or 0) * 10.0)

        comps = [
            ScoreComponent("extension", price_5m, ext_risk * 0.40, "延伸幅度"),
            ScoreComponent("retrace", retrace, retrace_risk * 0.35, "回撤风险"),
            ScoreComponent("local_extension", context_5m, local_risk * 0.25, "局部延伸"),
        ]
        score = _clamp(sum(c.contribution for c in comps))
        return SubScore("chase_risk", "追涨风险", score, True, comps, is_risk=True)
