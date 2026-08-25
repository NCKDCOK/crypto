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


class FreshnessBudget(BaseModel):
    """每流的 freshness budget（超过即 STALE）。"""

    aggtrade_active_ms: int = Field(default=5_000, ge=100)
    aggtrade_low_activity_ms: int = Field(default=30_000, ge=100)
    kline_1m_ms: int = Field(default=90_000, ge=1000)
    oi_poller_multiplier: int = Field(default=2, ge=1)
    funding_premium_ms: int = Field(default=60_000, ge=1000)
    oi_lookup_tolerance_ms: int = Field(default=15_000, ge=100)


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
    activity_tiers: dict[str, float] = Field(
        default_factory=lambda: {
            "active": 50_000_000,  # 24h 成交额 > 50M USDT
            "normal": 5_000_000,   # > 5M
        }
    )


class DetectorsConfig(BaseModel):
    """检测器阈值配置（V1 仅 anomaly 阈值，其余后续 Gate 补充）。"""

    anomaly_volume_z: float = Field(default=3.0, gt=0)
    anomaly_trade_count_z: float = Field(default=3.0, gt=0)
    anomaly_price_accel_z: float = Field(default=2.5, gt=0)
    anomaly_taker_delta_z: float = Field(default=2.5, gt=0)


class AppConfigBundle(BaseModel):
    """全部配置的聚合。"""

    app: AppConfig = Field(default_factory=AppConfig)
    data_health: DataHealthConfig = Field(default_factory=DataHealthConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    state_machine: StateMachineConfig = Field(default_factory=StateMachineConfig)
    symbols: SymbolsConfig = Field(default_factory=SymbolsConfig)
    detectors: DetectorsConfig = Field(default_factory=DetectorsConfig)


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
    }
    raw: dict[str, Any] = {}
    for key, filename in files.items():
        path = configs_dir / filename
        if path.exists():
            raw[key] = load_yaml(path)
    return AppConfigBundle(**raw)
