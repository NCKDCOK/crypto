"""配置加载与 Pydantic schema 校验。

所有阈值集中管理，禁止 magic number 散落在业务代码。
配置 schema 校验失败 ⇒ fail closed 不启动。
依据：ARCHITECTURE.md §6, docs/DATA_HEALTH.md, docs/STATE_MACHINE.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


# ────────────────────────────────────────────────────────────────────
# Schema 定义
# ────────────────────────────────────────────────────────────────────


class AppConfig(BaseModel):
    """全局应用配置。"""

    exchange: str = Field(default="binance")
    ws_base_url: str = Field(default="wss://fstream.binance.com")
    ws_route_market: str = Field(default="/market")
    ws_route_public: str = Field(default="/public")
    rest_base_url: str = Field(default="https://fapi.binance.com")
    max_streams_per_connection: int = Field(default=1024, ge=1)
    connection_max_lifetime_hours: float = Field(default=24.0, gt=0)
    # 出口代理（受限环境用）；空字符串/None 表示直连
    proxy: str | None = Field(default=None)
    # V1.2 §9 现货 endpoints
    spot_ws_base_url: str = Field(default="wss://stream.binance.com:9443")
    spot_rest_base_url: str = Field(default="https://api.binance.com")
    enable_spot: bool = Field(default=True, description="是否启用现货数据采集")
    # 编排节奏（秒）
    light_scan_interval_s: float = Field(default=20.0, gt=0)
    deep_compute_interval_s: float = Field(default=2.0, gt=0)
    candidate_refresh_interval_s: float = Field(default=60.0, gt=0)
    deep_max_symbols: int = Field(default=40, ge=1, le=1024)
    # 本地持久化数据目录（V1.2 停机恢复）
    data_dir: str = Field(default="data", description="SQLite 等本地数据存放目录")


class RecoveryConfig(BaseModel):
    """停机恢复配置（V1.2）。三档恢复策略。"""

    # 停机时间分档（秒）
    tier_quick_s: float = Field(default=300.0, gt=0, description="<5min 快速恢复")
    tier_full_s: float = Field(default=3600.0, gt=0, description="5min~1h 补历史重算；>1h 全部失效重建")
    # 回填 K 线根数（每周期）
    backfill_kline_limit: int = Field(default=300, ge=10, le=1500)
    # 进入 LIVE 的最小实时样本（OI/CVD/Delta 预热）
    live_min_samples: int = Field(default=10, ge=1)
    # Trade Plan 过期阈值（停机超过此值 → 旧 plan 标 EXPIRED）
    trade_plan_expire_s: float = Field(default=600.0, gt=0)


class MarketRegimeConfig(BaseModel):
    """市场背景引擎配置（V1.2 §8）。NOTE: uncalibrated 初值。"""

    btc_dominant_return: float = Field(default=0.01, description="BTC 1h 涨幅超此值且强于山寨 → BTC 主导")
    btc_dominant_gap: float = Field(default=0.01, description="BTC 领先山寨的差距")
    panic_return: float = Field(default=0.03, description="BTC 1h 跌幅超此值 → 恐慌")
    panic_down_ratio: float = Field(default=0.7, ge=0, le=1)
    deleverage_return: float = Field(default=0.015, description="BTC 1h 跌幅超此值 → 去杠杆")
    deleverage_oi_contract: float = Field(default=0.4, ge=0, le=1, description="OI 收缩比例超此值")
    risk_on_up_ratio: float = Field(default=0.55, ge=0, le=1)
    risk_on_anomaly_ratio: float = Field(default=0.1, ge=0, le=1)
    risk_off_down_ratio: float = Field(default=0.55, ge=0, le=1)
    chop_anomaly_ratio: float = Field(default=0.05, ge=0, le=1)
    chop_breadth_balance: float = Field(default=0.1, ge=0, le=1)


class FreshnessBudget(BaseModel):
    """每流的 freshness budget（超过即 STALE）。"""

    aggtrade_active_ms: int = Field(default=5_000, ge=100)
    aggtrade_low_activity_ms: int = Field(default=30_000, ge=100)
    kline_1m_ms: int = Field(default=90_000, ge=1000)
    oi_poller_multiplier: int = Field(default=2, ge=1)
    funding_premium_ms: int = Field(default=60_000, ge=1000)
    oi_lookup_tolerance_ms: int = Field(default=15_000, ge=100)
    # OI / Funding 轮询周期（秒）
    oi_poll_interval_s: float = Field(default=5.0, gt=0)
    funding_poll_interval_s: float = Field(default=30.0, gt=0)


class RateLimiterConfig(BaseModel):
    """REST 限频配置。"""

    weight_limit_per_minute: int = Field(default=1200, ge=1)
    initial_backoff_ms: int = Field(default=1000, ge=100)
    max_backoff_ms: int = Field(default=60_000, ge=1000)
    circuit_breaker_threshold: int = Field(default=3, ge=1)


class DataHealthConfig(BaseModel):
    """Data Health 配置。"""

    freshness: FreshnessBudget = Field(default_factory=FreshnessBudget)
    rate_limiter: RateLimiterConfig = Field(default_factory=RateLimiterConfig)


class FeaturesConfig(BaseModel):
    """特征配置。"""

    baseline_window: str = Field(default="1h")
    epsilon: float = Field(default=1.0, gt=0, description="FlowImpact 的 ε")
    trade_flow_windows: list[str] = Field(
        default_factory=lambda: ["5s", "15s", "30s", "1m", "5m"]
    )
    kline_context_intervals: list[str] = Field(
        default_factory=lambda: ["1m", "5m", "15m", "1h"]
    )
    baseline_max_samples: int = Field(default=360, ge=10)


class StateMachineConfig(BaseModel):
    """状态机配置。依据 docs/STATE_MACHINE.md §10.2。"""

    anomaly_decay_window: str = Field(default="30s")
    confirmation_hold: str = Field(default="15s")
    continuation_hold: str = Field(default="30s")
    exhaustion_confirm: str = Field(default="1m")
    withdrawal_confirm: str = Field(default="1m")
    cooldown_seconds: int = Field(default=300, ge=0)


class SymbolsConfig(BaseModel):
    """Symbol 过滤与分级配置。"""

    quote_asset: str = Field(default="USDT")
    exclude_patterns: list[str] = Field(default_factory=list)
    whitelist: list[str] = Field(default_factory=list)
    liquidity_floor_usdt: float = Field(default=5_000_000, ge=0)
    top_n: int = Field(default=100, ge=1)
    max_symbols: int = Field(default=100, ge=1)
    activity_tiers: dict[str, float] = Field(
        default_factory=lambda: {
            "active": 50_000_000,  # 24h 成交额 > 50M USDT
            "normal": 5_000_000,   # > 5M
        }
    )


class DetectorsConfig(BaseModel):
    """检测器阈值配置。"""

    # anomaly
    anomaly_volume_z: float = Field(default=3.0, gt=0)
    anomaly_trade_count_z: float = Field(default=3.0, gt=0)
    anomaly_price_accel_z: float = Field(default=2.5, gt=0)
    anomaly_taker_delta_z: float = Field(default=2.5, gt=0)
    # startup
    startup_oi_expansion_threshold: float = Field(default=0.0)
    startup_min_efficiency: float = Field(default=0.2, ge=0)
    startup_max_retrace: float = Field(default=0.8, ge=0)
    startup_min_evidence: int = Field(default=3, ge=1)
    # false start veto
    veto_rapid_retrace_threshold: float = Field(default=0.7, ge=0)
    veto_absorption_flow_impact_threshold: float = Field(default=0.001, gt=0)
    veto_absorption_delta_threshold: float = Field(default=10000.0, gt=0)
    veto_crowding_percentile_threshold: float = Field(default=95.0, ge=0, le=100)
    veto_one_bar_spike_window: str = Field(default="5s")
    veto_one_bar_spike_retrace: float = Field(default=0.6, ge=0)
    # continuation / exhaustion / withdrawal
    continuation_min_oi_maintain: float = Field(default=0.0)
    continuation_min_evidence_count: int = Field(default=2, ge=1, description="V1.2 §21 真实证据化最低通过数")
    exhaustion_min_divergence_count: int = Field(default=2, ge=1)
    withdrawal_min_evidence_count: int = Field(default=3, ge=1)
    # light scanner (Stage1) — 短时增量异动
    light_volume_delta_z: float = Field(default=2.5, gt=0, description="ΔQuoteVolume z-score 阈值")
    light_trade_count_delta_z: float = Field(default=2.5, gt=0, description="ΔTradeCount z-score 阈值")
    light_price_delta_z: float = Field(default=2.0, gt=0, description="ΔPrice z-score 阈值")
    light_min_anomaly_signals: int = Field(default=1, ge=1, description="至少 N 个增量信号超阈值才成候选")
    # legacy（兼容旧测试）
    light_relative_volume_z: float = Field(default=2.5, gt=0)
    light_trade_count_z: float = Field(default=2.5, gt=0)
    light_oi_change_threshold: float = Field(default=0.02, ge=0)


class HysteresisConfig(BaseModel):
    """Candidate 防抖配置 — P0.4。"""

    min_dwell_s: float = Field(default=180.0, gt=0, description="最低驻留秒数（3 分钟）")
    min_consecutive_drops: int = Field(default=3, ge=1, description="连续跌出阈值 N 次后才移除")
    max_deep_symbols: int = Field(default=40, ge=1, le=1024, description="深度分析上限")


class ScoringConfig(BaseModel):
    """评分权重配置 — 全部配置化，禁止 magic number。"""

    # 基础机会分权重（合计应归一化）
    w_capital_inflow: float = Field(default=0.22, ge=0, le=1)
    w_startup_quality: float = Field(default=0.20, ge=0, le=1)
    w_trend: float = Field(default=0.15, ge=0, le=1)
    w_immediate_stamina: float = Field(default=0.13, ge=0, le=1)
    w_sustained_startup: float = Field(default=0.12, ge=0, le=1)
    w_anomaly_intensity: float = Field(default=0.10, ge=0, le=1)
    w_chase_safety: float = Field(default=0.08, ge=0, le=1)
    # 风险扣分权重
    w_top_risk: float = Field(default=0.35, ge=0, le=1)
    w_crowding_risk: float = Field(default=0.20, ge=0, le=1)
    w_withdrawal_risk: float = Field(default=0.30, ge=0, le=1)
    w_chase_risk: float = Field(default=0.15, ge=0, le=1)
    # 风险扣分缩放
    risk_penalty_scale: float = Field(default=0.4, ge=0, le=1, description="风险分对机会分的扣减比例")
    # 评分预热
    warmup_min_samples: int = Field(default=10, ge=1, description="最小样本数才开始评分")
    # ── Data Confidence / 数据可信度（V1.2 §3.3）──
    # NOTE: 以下权重为 uncalibrated 初值，待 Replay Calibration（P23）校准。
    data_confidence_base: float = Field(default=100.0, ge=0, le=100)
    data_confidence_unknown_penalty: float = Field(default=40.0, ge=0, le=100, description="关键流 STALE/FAIL 扣分（须低于 strong_confirm 门）")
    data_confidence_degraded_penalty: float = Field(default=10.0, ge=0, le=100)
    data_confidence_stale_penalty: float = Field(default=15.0, ge=0, le=100, description="stale_flag 命中扣分")
    data_confidence_missing_oi_penalty: float = Field(default=15.0, ge=0, le=100)
    data_confidence_missing_funding_penalty: float = Field(default=5.0, ge=0, le=100)
    data_confidence_missing_kline_penalty: float = Field(default=8.0, ge=0, le=100)
    data_confidence_missing_spot_penalty: float = Field(default=5.0, ge=0, le=100)
    data_confidence_queue_lag_penalty: float = Field(default=5.0, ge=0, le=100)
    data_confidence_queue_lag_penalty_ms: float = Field(default=2000.0, ge=0, description="队列延迟超此值才扣分")
    # ── Signal Confirmation / 信号确认度（V1.2 §3.2）──
    # NOTE: uncalibrated 初值，待 Replay Calibration（P23）校准。
    sc_core_weight: float = Field(default=0.45, ge=0, le=1)
    sc_supporting_weight: float = Field(default=0.25, ge=0, le=1)
    sc_veto_weight: float = Field(default=0.15, ge=0, le=1)
    sc_multitf_weight: float = Field(default=0.15, ge=0, le=1)
    sc_volume_z_threshold: float = Field(default=2.0, ge=0, description="辅助证据：成交量异常阈值")
    sc_acceptance_threshold: float = Field(default=0.5, ge=0, le=1, description="突破有效性阈值")
    sc_retrace_healthy: float = Field(default=0.4, ge=0, le=1, description="回踩健康阈值（retrace_ratio 上限）")
    sc_spot_agreement_threshold: float = Field(default=0.3, ge=-1, le=1, description="现货合约一致性阈值")
    sc_strong_confirm_min_dc: float = Field(default=70.0, ge=0, le=100, description="强确认所需最低 data_confidence")


class AppConfigBundle(BaseModel):
    """全部配置的聚合。"""

    app: AppConfig = Field(default_factory=AppConfig)
    data_health: DataHealthConfig = Field(default_factory=DataHealthConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    state_machine: StateMachineConfig = Field(default_factory=StateMachineConfig)
    symbols: SymbolsConfig = Field(default_factory=SymbolsConfig)
    detectors: DetectorsConfig = Field(default_factory=DetectorsConfig)
    hysteresis: HysteresisConfig = Field(default_factory=HysteresisConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    market_regime: MarketRegimeConfig = Field(default_factory=MarketRegimeConfig)


# ────────────────────────────────────────────────────────────────────
# 加载函数
# ────────────────────────────────────────────────────────────────────


def load_yaml(path: Path) -> dict[str, Any]:
    """加载单个 yaml 文件为 dict。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def load_config(configs_dir: Path) -> AppConfigBundle:
    """从 configs/ 目录加载全部配置。

    读取 app.yaml / data_health.yaml / features.yaml / state_machine.yaml /
    symbols.yaml / detectors.yaml，合并并校验。
    缺失的文件使用默认值。
    """
    files = {
        "app": "app.yaml",
        "data_health": "data_health.yaml",
        "features": "features.yaml",
        "state_machine": "state_machine.yaml",
        "symbols": "symbols.yaml",
        "detectors": "detectors.yaml",
        "hysteresis": "hysteresis.yaml",
        "scoring": "scoring.yaml",
    }
    raw: dict[str, Any] = {}
    for key, filename in files.items():
        path = configs_dir / filename
        if path.exists():
            raw[key] = load_yaml(path)
    return AppConfigBundle(**raw)
