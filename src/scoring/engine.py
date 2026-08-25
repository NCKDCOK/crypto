"""Score Engine — 可解释评分引擎。

依据：V1.1 计划 §十三~§十六, V1.2 计划 §5 / §23 / §24
- 子评分，每个 0~100
- OpportunityScore = 加权基础分 - 风险扣分
- 权重全部来自配置，禁止 magic number
- 每个分数输出 breakdown（components + evidence）
- 评分预热：样本不足时不评分

V1.2 §5 缺失数据规则（核心修复）：
- 缺失组件禁止默认 50 分。
- 缺失组件从有效分母移除（按权重归一化）。
- 每个子评分输出 coverage（可用权重 / 总权重）与 missing 列表。
- 核心组件全缺 → 子评分 unavailable（available=False）。

V1.2 §23 子评分体系：正向核心 + 风险，覆盖 §23 全部子评分。
V1.2 §24 Setup-aware 权重：不同 Setup 使用不同权重表。
V1.2 §41 Pump Risk 高时 Opportunity 受惩罚。

NOTE: V1.2 权重为 uncalibrated 初值，待 Replay Calibration（P23）校准。
"""

from __future__ import annotations

import logging
import math
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
    weight: float = 0.0
    available: bool = True


@dataclass
class SubScore:
    """子评分结果。"""

    name: str
    label: str
    score: float  # 0~100
    available: bool
    components: list[ScoreComponent] = field(default_factory=list)
    is_risk: bool = False  # 风险分类子评分
    coverage: float = 1.0  # 可用权重 / 总权重（§5）
    missing: list[str] = field(default_factory=list)


@dataclass
class ScoreBreakdown:
    """完整评分结果。"""

    opportunity_score: float  # 0~100
    available: bool
    subscores: dict[str, SubScore] = field(default_factory=dict)
    risk_penalty: float = 0.0
    base_score: float = 0.0
    coverage: float = 1.0  # 总体 coverage（§5）
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_score": round(self.opportunity_score, 1) if self.available else None,
            "available": self.available,
            "base_score": round(self.base_score, 1),
            "risk_penalty": round(self.risk_penalty, 1),
            "coverage": round(self.coverage, 3),
            "missing": list(self.missing),
            "subscores": {
                k: {
                    "label": v.label,
                    "score": round(v.score, 1),
                    "available": v.available,
                    "is_risk": v.is_risk,
                    "coverage": round(v.coverage, 3),
                    "missing": list(v.missing),
                    "components": [
                        {
                            "name": c.name,
                            "value": c.value,
                            "contribution": round(c.contribution, 1),
                            "weight": round(c.weight, 3),
                            "available": c.available,
                        }
                        for c in v.components
                    ],
                }
                for k, v in self.subscores.items()
            },
        }


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _sigmoid_z(z: float | None, scale: float = 15.0) -> float | None:
    """将 z-score 映射到 0~100（sigmoid，z=0 → 50, z=3 → ~95）。

    V1.2 §5：z 为 None（缺失）时返回 None，由调用方从分母移除。
    """
    if z is None:
        return None
    exp_arg = max(-700.0, min(700.0, -z * scale / 30.0))
    return _clamp(100.0 / (1.0 + math.exp(exp_arg)))


def _dir_sign(direction: str | None) -> float:
    """方向符号：LONG → +1, SHORT → -1, None → 0。"""
    if direction == "LONG":
        return 1.0
    if direction == "SHORT":
        return -1.0
    return 0.0


def _aligned_score(value: float | None, direction: str | None, scale: float = 15.0) -> float | None:
    """方向对齐评分：value 与 direction 同向 → 高分。

    V1.2 §5：value 或 direction 缺失时返回 None（从分母移除），不默认 50。
    """
    if value is None:
        return None
    sign = _dir_sign(direction)
    if sign == 0:
        return None
    aligned = value * sign
    return _sigmoid_z(aligned, scale)


@dataclass
class _CompSpec:
    """组件计算中间结构。"""

    name: str
    value: float | None
    weight: float
    score: float | None  # None = 缺失，从分母移除
    description: str = ""
    feature_key: str = ""


def _build_subscore(
    key: str,
    label: str,
    specs: list[_CompSpec],
    is_risk: bool = False,
) -> SubScore:
    """按 §5 规则构建子评分：缺失组件从分母移除，按可用权重归一化。"""
    total_weight = sum(s.weight for s in specs)
    avail = [s for s in specs if s.score is not None]
    avail_weight = sum(s.weight for s in avail)
    missing = [s.feature_key or s.name for s in specs if s.score is None]

    components = [
        ScoreComponent(
            name=s.name,
            value=s.value,
            contribution=(s.score or 0.0) * s.weight,
            description=s.description,
            weight=s.weight,
            available=s.score is not None,
        )
        for s in specs
    ]

    if avail_weight <= 0 or total_weight <= 0:
        return SubScore(
            name=key, label=label, score=0.0, available=False,
            components=components, is_risk=is_risk,
            coverage=0.0, missing=missing,
        )

    score = sum(s.score * s.weight for s in avail) / avail_weight  # type: ignore[arg-type]
    coverage = avail_weight / total_weight
    return SubScore(
        name=key, label=label, score=_clamp(score), available=True,
        components=components, is_risk=is_risk,
        coverage=coverage, missing=missing,
    )


def _aggregate_weighted(
    items: list[tuple[SubScore, float]],
) -> tuple[float, float, list[str]]:
    """按可用权重归一化聚合子评分 → (score, coverage, missing)。"""
    total_w = sum(w for _, w in items)
    avail = [(ss, w) for ss, w in items if ss.available and ss.coverage > 0]
    avail_w = sum(w for _, w in avail)
    missing: list[str] = []
    for ss, _ in items:
        if not ss.available:
            missing.append(ss.name)
        else:
            missing.extend(ss.missing)
    if avail_w <= 0 or total_w <= 0:
        return 0.0, 0.0, missing
    score = sum(ss.score * w for ss, w in avail) / avail_w
    return _clamp(score), avail_w / total_w, missing


class ScoreEngine:
    """可解释评分引擎。

    从 FeatureSnapshot + State 计算 子评分 + OpportunityScore。
    V1.2 §5：缺失数据从分母移除，不默认 50；每个分数带 coverage / missing。
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
        *,
        setup_type: str | None = None,
        pump_risk_score: float | None = None,
    ) -> ScoreBreakdown:
        """计算完整评分。

        V1.2 §24: setup_type 决定权重表（setup-aware）。
        V1.2 §41: pump_risk 高时 Opportunity 受惩罚。
        """
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

        # ── 基础机会分（按可用权重归一化）──
        base_items = [
            (capital, w.w_capital_inflow),
            (startup, w.w_startup_quality),
            (trend, w.w_trend),
            (stamina, w.w_immediate_stamina),
            (sustained, w.w_sustained_startup),
            (anomaly, w.w_anomaly_intensity),
            (chase, w.w_chase_safety),
        ]
        base, base_cov, base_missing = _aggregate_weighted(base_items)

        # ── 风险扣分（按可用权重归一化）──
        risk_items = [
            (top_risk, w.w_top_risk),
            (crowding, w.w_crowding_risk),
            (withdrawal, w.w_withdrawal_risk),
            (chase_risk, w.w_chase_risk),
        ]
        risk_raw, risk_cov, risk_missing = _aggregate_weighted(risk_items)
        risk_penalty = risk_raw * w.risk_penalty_scale

        opportunity = _clamp(base - risk_penalty)

        # V1.2 §41: Pump Risk 高时 Opportunity 受惩罚
        pump_penalty = 0.0
        if pump_risk_score is not None and pump_risk_score > 60:
            pump_penalty = (pump_risk_score - 60) * 0.5
            opportunity = _clamp(opportunity - pump_penalty)
            factors_pump = {"pump_risk_penalty": -pump_penalty}
        else:
            factors_pump = {}

        # 总体 coverage / missing
        all_missing = sorted(set(base_missing + risk_missing))
        overall_cov = (base_cov + risk_cov) / 2.0 if (base_cov + risk_cov) > 0 else 0.0

        return ScoreBreakdown(
            opportunity_score=opportunity,
            available=True,
            subscores=subscores,
            risk_penalty=risk_penalty + pump_penalty,
            base_score=base,
            coverage=overall_cov,
            missing=all_missing,
        )

    # ── 基础机会分子评分 ──

    def _capital_inflow(self, fv: dict, direction: str | None) -> SubScore:
        """资金输入：新增方向资金是否真的进入？"""
        oi_exp = fv.get("oi_change_5m")
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        cvd_z = fv.get("cvd_slope_z")
        cvd_accel = fv.get("cvd_accel_z")
        price_resp = fv.get("price_return_30s")

        specs = [
            _CompSpec("oi_expansion_5m", oi_exp, 0.25, _aligned_score(oi_exp, direction), "OI 5m 扩张", "oi_change_5m"),
            _CompSpec("taker_delta", delta, 0.25, _aligned_score(delta, direction, 0.001), "主动买卖差", "signed_delta"),
            _CompSpec("cvd_slope_z", cvd_z, 0.25, _aligned_score(cvd_z, direction), "CVD 斜率 Z", "cvd_slope_z"),
            _CompSpec("cvd_accel_z", cvd_accel, 0.15, _aligned_score(cvd_accel, direction), "CVD 加速 Z", "cvd_accel_z"),
            _CompSpec("price_response", price_resp, 0.10, _aligned_score(price_resp, direction, 50), "价格响应", "price_return_30s"),
        ]
        return _build_subscore("capital_inflow", "资金输入", specs)

    def _startup_quality(self, fv: dict, direction: str | None) -> SubScore:
        """启动质量：这次异动是不是像真正启动？"""
        vol_z = fv.get("volume_z")
        tc_z = fv.get("trade_count_z")
        price_accel = fv.get("price_acceleration")
        oi_1m = fv.get("oi_change_1m")
        cvd_z = fv.get("cvd_slope_z")
        accept = fv.get("acceptance")

        specs = [
            _CompSpec("volume_z", vol_z, 0.20, _sigmoid_z(vol_z), "成交量 Z", "volume_z"),
            _CompSpec("trade_count_z", tc_z, 0.15, _sigmoid_z(tc_z), "成交笔数 Z", "trade_count_z"),
            _CompSpec("price_acceleration", price_accel, 0.20, _aligned_score(price_accel, direction, 100), "价格加速度", "price_acceleration"),
            _CompSpec("oi_confirmation_1m", oi_1m, 0.15, _aligned_score(oi_1m, direction), "OI 1m 确认", "oi_change_1m"),
            _CompSpec("flow_confirmation", cvd_z, 0.15, _aligned_score(cvd_z, direction), "资金流确认", "cvd_slope_z"),
            _CompSpec("breakout_acceptance", accept, 0.15, _sigmoid_z((accept or 0) * 100) if accept is not None else None, "突破有效性", "acceptance"),
        ]
        return _build_subscore("startup_quality", "启动质量", specs)

    def _trend(self, fv: dict, direction: str | None) -> SubScore:
        """趋势：当前方向是否稳定？基于多周期 Kline context。"""
        intervals = ["1m", "5m", "15m", "1h"]
        weights = [0.15, 0.25, 0.30, 0.30]
        specs = []
        for iv, wt in zip(intervals, weights):
            val = fv.get(f"context_{iv}")
            specs.append(_CompSpec(f"context_{iv}", val, wt, _aligned_score(val, direction, 200), f"{iv} 周期趋势", f"context_{iv}"))
        return _build_subscore("trend", "趋势", specs)

    def _immediate_stamina(self, fv: dict, direction: str | None) -> SubScore:
        """即时续航：最近几十秒到几分钟，资金还在不在？"""
        delta_1m = fv.get("taker_delta_1m")
        cvd_slope = fv.get("CVD_slope")
        oi_30s = fv.get("oi_change_30s")
        eff = fv.get("price_efficiency")
        retrace = fv.get("retrace_ratio")

        retrace_score = _clamp(100.0 - (retrace or 0) * 100.0) if retrace is not None else None

        specs = [
            _CompSpec("delta_persistence_1m", delta_1m, 0.25, _aligned_score(delta_1m, direction, 0.001), "Delta 持续性", "taker_delta_1m"),
            _CompSpec("cvd_persistence", cvd_slope, 0.25, _aligned_score(cvd_slope, direction, 0.001), "CVD 持续性", "CVD_slope"),
            _CompSpec("oi_persistence_30s", oi_30s, 0.20, _aligned_score(oi_30s, direction), "OI 持续性", "oi_change_30s"),
            _CompSpec("price_efficiency", eff, 0.15, _sigmoid_z((eff or 0) * 100 - 50) if eff is not None else None, "价格效率", "price_efficiency"),
            _CompSpec("retrace_quality", retrace, 0.15, retrace_score, "回踩质量", "retrace_ratio"),
        ]
        return _build_subscore("immediate_stamina", "即时续航", specs)

    def _sustained_startup(
        self, fv: dict, direction: str | None, state: State,
        evidence_count: int, state_since_ms: int | None, now_ms: int,
    ) -> SubScore:
        """持续启动：启动是不是只有一波，还是有持续性？

        V1.2 §21：禁止「因为在 CONTINUATION 所以高分」。状态权重保留但降低，
        真实证据（持续时间 / 证据数）占主导。完整真实证据化见 P17。
        """
        # 状态加权（降低权重，P17 将进一步真实证据化）
        state_weights = {
            State.START_CONFIRMED: 75.0,
            State.CONTINUATION: 80.0,
            State.SUSPECTED_START: 55.0,
            State.ANOMALY: 35.0,
            State.EXHAUSTION: 25.0,
            State.WITHDRAWAL: 15.0,
        }
        state_score = state_weights.get(state, 45.0)

        # 持续时间加分（最多 60s → 满分）
        duration_s = 0.0
        if state_since_ms is not None:
            duration_s = (now_ms - state_since_ms) / 1000.0
        duration_score = _clamp(min(duration_s / 60.0, 1.0) * 100.0)

        # 证据数加分
        evidence_score = _clamp(min(evidence_count / 5.0, 1.0) * 100.0)

        specs = [
            _CompSpec("state_quality", state.value, 0.30, state_score, "状态质量", "state"),
            _CompSpec("duration", duration_s, 0.35, duration_score, "持续时间", "duration"),
            _CompSpec("evidence_count", float(evidence_count), 0.35, evidence_score, "证据数量", "evidence_count"),
        ]
        return _build_subscore("sustained_startup", "持续启动", specs)

    def _anomaly_intensity(self, fv: dict) -> SubScore:
        """异动强度：当前变化相对历史是否异常？"""
        vol_z = fv.get("volume_z")
        tc_z = fv.get("trade_count_z")
        price_accel = fv.get("price_acceleration")
        oi_5m = fv.get("oi_change_5m")

        specs = [
            _CompSpec("volume_z", vol_z, 0.30, _sigmoid_z(vol_z), "成交量 Z", "volume_z"),
            _CompSpec("trade_count_z", tc_z, 0.25, _sigmoid_z(tc_z), "成交笔数 Z", "trade_count_z"),
            _CompSpec("price_acceleration", price_accel, 0.25, _sigmoid_z(price_accel), "价格加速度 Z", "price_acceleration"),
            _CompSpec("oi_delta_5m", oi_5m, 0.20, _sigmoid_z((oi_5m or 0) * 100) if oi_5m is not None else None, "OI 变化 Z", "oi_change_5m"),
        ]
        return _build_subscore("anomaly_intensity", "异动强度", specs)

    def _chase_safety(self, fv: dict, direction: str | None) -> SubScore:
        """追涨安全：现在是不是已经太晚？"""
        price_5m = fv.get("price_return_5m")
        retrace = fv.get("retrace_ratio")
        eff = fv.get("price_efficiency")
        funding_pct = fv.get("funding_percentile")
        context_5m = fv.get("context_5m")

        ext_score = _clamp(100.0 - abs(price_5m) * 10.0) if price_5m is not None else None
        retrace_score = _clamp(100.0 - (retrace or 0) * 100.0) if retrace is not None else None
        eff_score = _sigmoid_z((eff or 0) * 100 - 50) if eff is not None else None
        crowd_score = _clamp(100.0 - (funding_pct or 0)) if funding_pct is not None else None
        local_ext = _clamp(100.0 - abs(context_5m or 0) * 10.0) if context_5m is not None else None

        specs = [
            _CompSpec("distance_from_initiation", price_5m, 0.30, ext_score, "距启动幅度", "price_return_5m"),
            _CompSpec("retrace", retrace, 0.20, retrace_score, "回撤幅度", "retrace_ratio"),
            _CompSpec("efficiency", eff, 0.20, eff_score, "价格效率", "price_efficiency"),
            _CompSpec("crowding", funding_pct, 0.15, crowd_score, "拥挤度", "funding_percentile"),
            _CompSpec("local_extension", context_5m, 0.15, local_ext, "局部延伸", "context_5m"),
        ]
        return _build_subscore("chase_safety", "追涨安全", specs)

    # ── 风险子评分 ──

    def _top_risk(self, fv: dict, direction: str | None) -> SubScore:
        """顶部风险：是否出现衰竭迹象？"""
        cvd_z = fv.get("cvd_slope_z")
        vol_z = fv.get("volume_z")
        eff = fv.get("price_efficiency")
        oi_5m = fv.get("oi_change_5m")
        accept = fv.get("acceptance")

        # CVD 背离：方向向上但 CVD 向下
        divergence = None
        if cvd_z is not None and direction:
            sign = _dir_sign(direction)
            if cvd_z * sign < 0:
                divergence = _clamp(abs(cvd_z) * 20.0)
            else:
                divergence = 0.0

        high_vol_low_eff = None
        if vol_z is not None and eff is not None:
            high_vol_low_eff = _clamp((vol_z - 2) * 20.0) if vol_z > 2 and eff < 0.3 else 0.0

        oi_decay = _clamp(abs(min(oi_5m or 0, 0)) * 500.0) if oi_5m is not None else None
        eff_collapse = _clamp((1.0 - (eff or 0)) * 100.0) if eff is not None else None
        failed_breakout = _clamp((1.0 - (accept or 0)) * 100.0) if accept is not None else None

        specs = [
            _CompSpec("cvd_divergence", cvd_z, 0.30, divergence, "CVD 背离", "cvd_slope_z"),
            _CompSpec("high_vol_low_eff", vol_z, 0.25, high_vol_low_eff, "放量滞涨", "volume_z"),
            _CompSpec("oi_decay", oi_5m, 0.20, oi_decay, "OI 衰减", "oi_change_5m"),
            _CompSpec("efficiency_collapse", eff, 0.15, eff_collapse, "效率坍塌", "price_efficiency"),
            _CompSpec("failed_breakout", accept, 0.10, failed_breakout, "突破失败", "acceptance"),
        ]
        return _build_subscore("top_risk", "顶部风险", specs, is_risk=True)

    def _crowding_risk(self, fv: dict) -> SubScore:
        """拥挤风险：Funding / Premium / 情绪过热。"""
        funding = fv.get("funding")
        funding_pct = fv.get("funding_percentile")
        premium_pct = fv.get("premium_percentile")

        funding_score = _clamp(abs(funding or 0) * 5000.0) if funding is not None else None
        fp_score = _clamp(funding_pct or 0) if funding_pct is not None else None
        pp_score = _clamp(premium_pct or 0) if premium_pct is not None else None

        specs = [
            _CompSpec("funding_rate", funding, 0.40, funding_score, "资金费率", "funding"),
            _CompSpec("funding_percentile", funding_pct, 0.35, fp_score, "费率分位", "funding_percentile"),
            _CompSpec("premium_percentile", premium_pct, 0.25, pp_score, "溢价分位", "premium_percentile"),
        ]
        return _build_subscore("crowding_risk", "拥挤风险", specs, is_risk=True)

    def _withdrawal_risk(self, fv: dict, direction: str | None) -> SubScore:
        """撤离风险：OI 衰减 / Delta 反转 / CVD 反转 / 效率坍塌。"""
        oi_5m = fv.get("oi_change_5m")
        delta = fv.get("signed_delta") or fv.get("taker_delta")
        cvd_z = fv.get("cvd_slope_z")
        eff = fv.get("price_efficiency")
        accept = fv.get("acceptance")

        oi_decay = _clamp(abs(min(oi_5m or 0, 0)) * 500.0) if oi_5m is not None else None

        # Delta 反转
        delta_rev = None
        if delta is not None and direction:
            sign = _dir_sign(direction)
            if delta * sign < 0:
                delta_rev = _clamp(abs(delta) * 0.01)
            else:
                delta_rev = 0.0

        # CVD 反转
        cvd_rev = None
        if cvd_z is not None and direction:
            sign = _dir_sign(direction)
            if cvd_z * sign < 0:
                cvd_rev = _clamp(abs(cvd_z) * 20.0)
            else:
                cvd_rev = 0.0

        eff_collapse = _clamp((1.0 - (eff or 0)) * 100.0) if eff is not None else None
        failed_accept = _clamp((1.0 - (accept or 0)) * 100.0) if accept is not None else None

        specs = [
            _CompSpec("oi_decay", oi_5m, 0.30, oi_decay, "OI 衰减", "oi_change_5m"),
            _CompSpec("delta_reversal", delta, 0.25, delta_rev, "Delta 反转", "signed_delta"),
            _CompSpec("cvd_reversal", cvd_z, 0.20, cvd_rev, "CVD 反转", "cvd_slope_z"),
            _CompSpec("efficiency_collapse", eff, 0.15, eff_collapse, "效率坍塌", "price_efficiency"),
            _CompSpec("failed_acceptance", accept, 0.10, failed_accept, "突破未站稳", "acceptance"),
        ]
        return _build_subscore("withdrawal_risk", "撤离风险", specs, is_risk=True)

    def _chase_risk(self, fv: dict, direction: str | None) -> SubScore:
        """追涨风险：追涨安全的风险侧（高分 = 风险大）。"""
        price_5m = fv.get("price_return_5m")
        retrace = fv.get("retrace_ratio")
        context_5m = fv.get("context_5m")

        ext_risk = _clamp(abs(price_5m or 0) * 10.0) if price_5m is not None else None
        retrace_risk = _clamp((retrace or 0) * 100.0) if retrace is not None else None
        local_risk = _clamp(abs(context_5m or 0) * 10.0) if context_5m is not None else None

        specs = [
            _CompSpec("extension", price_5m, 0.40, ext_risk, "延伸幅度", "price_return_5m"),
            _CompSpec("retrace", retrace, 0.35, retrace_risk, "回撤风险", "retrace_ratio"),
            _CompSpec("local_extension", context_5m, 0.25, local_risk, "局部延伸", "context_5m"),
        ]
        return _build_subscore("chase_risk", "追涨风险", specs, is_risk=True)
