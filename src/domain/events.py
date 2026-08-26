"""事件对象 — 统一事件模型 contracts。

依据：docs/DATA_MODEL.md §1–§7
所有时间戳为 UTC 毫秒 (int64)。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AggressorSide,
    ConfidenceState,
    Direction,
    EvidenceFamily,
    HealthLevel,
    KlineInterval,
    State,
    VetoSeverity,
    VetoType,
)


class _EventBase(BaseModel):
    """所有事件对象的公共配置。"""

    model_config = ConfigDict(
        use_enum_values=False,  # 保留枚举类型用于 type-check
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )


# ────────────────────────────────────────────────────────────────────
# §1  TradeEvent
# ────────────────────────────────────────────────────────────────────


class TradeEvent(_EventBase):
    """逐笔成交。来源：Binance aggTrade WebSocket。

    Binance aggTrade payload 字段映射：
        e → event_type (固定 "aggTrade"，不在模型中存储)
        E → event_time
        s → symbol
        a → trade_id
        p → price
        q → qty
        f → first_trade_id (不在 V1 contract 中)
        l → last_trade_id  (不在 V1 contract 中)
        T → trade_time (即 event_time 中的成交时间)
        m → is_maker (买方是否为 maker)
        M → ignore
    """

    symbol: str = Field(..., min_length=1, description="如 BTCUSDT")
    exchange: str = Field(default="binance", description="V1 固定 binance")
    trade_id: int = Field(..., ge=0, description="Binance a；去重主键")
    event_time: int = Field(..., ge=0, description="Binance T；成交时间 UTC ms")
    receive_time: int = Field(..., ge=0, description="本地接收时间 UTC ms")
    price: Decimal = Field(..., description="Binance p；保留精度")
    qty: Decimal = Field(..., description="Binance q；基础资产数量")
    quote_notional: Decimal = Field(..., description="price × qty；本地计算")
    aggressor_side: AggressorSide = Field(..., description="由 m 派生")
    is_maker: bool = Field(..., description="Binance m 原值；买方是否为 maker")

    @model_validator(mode="after")
    def _check_quote_notional(self) -> TradeEvent:
        """quote_notional 必须等于 price × qty（允许 Decimal 精度误差）。"""
        expected = self.price * self.qty
        if self.quote_notional != expected:
            raise ValueError(
                f"quote_notional ({self.quote_notional}) != price × qty ({expected})"
            )
        return self


# ────────────────────────────────────────────────────────────────────
# §2  KlineEvent
# ────────────────────────────────────────────────────────────────────


class KlineEvent(_EventBase):
    """K 线。来源：Binance Kline WebSocket (``<symbol>@kline_<interval>``)。

    Binance kline payload 中 ``k`` 对象字段映射：
        t → open_time
        T → close_time
        s → symbol
        i → interval
        o → open
        c → close
        h → high
        l → low
        v → volume
        n → trade_count
        x → is_closed
        q → quote_volume
    """

    symbol: str = Field(..., min_length=1)
    exchange: str = Field(default="binance")
    interval: KlineInterval = Field(...)
    open_time: int = Field(..., ge=0, description="Binance t；K 线开盘时间")
    close_time: int = Field(..., ge=0, description="Binance T；K 线收盘时间")
    event_time: int = Field(..., ge=0, description="Binance E；事件推送时间")
    receive_time: int = Field(..., ge=0)
    open: Decimal = Field(...)
    high: Decimal = Field(...)
    low: Decimal = Field(...)
    close: Decimal = Field(...)
    volume: Decimal = Field(..., description="Binance v；基础资产成交量")
    quote_volume: Decimal | None = Field(default=None, description="Binance q")
    trade_count: int = Field(..., ge=0, description="Binance n")
    is_closed: bool = Field(..., description="Binance x；仅 closed bar 进入慢周期确认")

    @model_validator(mode="after")
    def _check_times(self) -> KlineEvent:
        if self.close_time < self.open_time:
            raise ValueError("close_time must be >= open_time")
        return self


# ────────────────────────────────────────────────────────────────────
# §3  OpenInterestSnapshot
# ────────────────────────────────────────────────────────────────────


class OpenInterestSnapshot(_EventBase):
    """持仓量快照。来源：Binance REST ``GET /fapi/v1/openInterest``。

    Binance 返回 ``openInterest`` 字段单位 = **基础资产数量**（如 0.5 BTC），
    不是合约张数，也不是美元名义。

    REST 响应字段映射：
        openInterest → open_interest (基础资产数量)
        symbol       → symbol
        time         → event_time (撮合引擎时间)
    """

    symbol: str = Field(..., min_length=1)
    exchange: str = Field(default="binance")
    event_time: int = Field(..., ge=0, description="快照对应市场时间")
    receive_time: int = Field(..., ge=0, description="本地接收时间")
    open_interest: Decimal = Field(..., description="基础资产数量，非美元名义")
    source: str = Field(default="binance_rest_openinterest")
    freshness_ms: int = Field(..., ge=0, description="距今年龄 = now - receive_time")


# ────────────────────────────────────────────────────────────────────
# §4  FundingRateSnapshot
# ────────────────────────────────────────────────────────────────────


class FundingRateSnapshot(_EventBase):
    """资金费率快照。来源：Binance REST ``GET /fapi/v1/premiumIndex``。

    REST 响应字段映射：
        symbol              → symbol
        markPrice           → mark_price
        indexPrice          → index_price
        estimatedSettlePrice → (不在 V1 contract)
        lastFundingRate     → last_funding_rate
        nextFundingTime     → next_funding_time
        time                → event_time
    premium = mark_price - index_price（本地计算）

    仅作 context / soft veto，不单独触发信号。
    """

    symbol: str = Field(..., min_length=1)
    exchange: str = Field(default="binance")
    event_time: int = Field(..., ge=0)
    receive_time: int = Field(..., ge=0)
    mark_price: Decimal = Field(...)
    index_price: Decimal = Field(...)
    last_funding_rate: Decimal = Field(..., description="小数，如 0.0001 = 0.01%")
    next_funding_time: int = Field(..., ge=0)
    premium: Decimal = Field(..., description="mark_price - index_price")
    source: str = Field(default="binance_rest_premiumindex")

    @model_validator(mode="after")
    def _check_premium(self) -> FundingRateSnapshot:
        expected = self.mark_price - self.index_price
        if self.premium != expected:
            raise ValueError(
                f"premium ({self.premium}) != mark_price - index_price ({expected})"
            )
        return self


# ────────────────────────────────────────────────────────────────────
# §4b  LongShortRatioSnapshot  (V1.4 §二十三)
# ────────────────────────────────────────────────────────────────────


class LongShortRatioSnapshot(_EventBase):
    """多空比快照。来源：Binance ``/futures/data/*LongShort*Ratio`` 系列。

    V1.4 §二十三：必须严格区分三个指标，禁止混为同一个 long_short_ratio。

    REST 响应字段映射（每端点返回 longShortRatio / longAccount / shortAccount）：
        globalLongShortAccountRatio → global_account_ls_ratio
        topLongShortAccountRatio    → top_trader_account_ls_ratio
        topLongShortPositionRatio   → top_trader_position_ls_ratio

    ratio = longAccount / shortAccount（>1 偏多，<1 偏空）。
    """

    symbol: str = Field(..., min_length=1)
    exchange: str = Field(default="binance")
    event_time: int = Field(..., ge=0)
    receive_time: int = Field(..., ge=0)
    global_account_ls_ratio: float | None = Field(default=None, description="普通账户多空比")
    top_trader_account_ls_ratio: float | None = Field(default=None, description="大户账户多空比")
    top_trader_position_ls_ratio: float | None = Field(default=None, description="大户持仓多空比")
    source: str = Field(default="binance_rest_longshort_ratio")


# ────────────────────────────────────────────────────────────────────
# §5  HealthStatus
# ────────────────────────────────────────────────────────────────────


class HealthStatus(_EventBase):
    """单个数据流的健康状态。每个 stream 独立维护。

    connected（socket 是否 open）与 healthy（OK）必须分开判断。
    connected ≠ healthy。
    """

    stream: str = Field(..., description="如 aggTrade:BTCUSDT")
    symbol: str | None = Field(default=None)
    status: HealthLevel = Field(...)
    last_event_time: int | None = Field(default=None)
    last_receive_time: int | None = Field(default=None)
    age_ms: int | None = Field(default=None, description="now - last_receive_time")
    stale_seconds: int | None = Field(default=None)
    connected: bool = Field(...)
    subscribed: bool | None = Field(default=None)
    message_count: int = Field(default=0, ge=0)
    reconnect_count: int = Field(default=0, ge=0)
    sequence: int | None = Field(default=None, description="仅 depth 类有")
    reason: str | None = Field(default=None)


# ────────────────────────────────────────────────────────────────────
# §6  FeatureSnapshot
# ────────────────────────────────────────────────────────────────────


class FeatureValue(_EventBase):
    """单个特征值。"""

    value: float | None = Field(default=None)
    available: bool = Field(...)
    window: str | None = Field(default=None)
    baseline_ref: dict[str, Any] | None = Field(default=None)


class FeatureSnapshot(_EventBase):
    """特征引擎输出。一个 symbol 在某时刻全部窗口特征的快照。"""

    symbol: str = Field(..., min_length=1)
    asof: int = Field(..., ge=0, description="快照生成时间 UTC ms")
    windows: dict[str, str] = Field(default_factory=dict)
    data_health: dict[str, Any] = Field(
        default_factory=dict, description="stream → status 摘要"
    )
    features: dict[str, FeatureValue] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(
        default_factory=dict, description="每个特征的来源快照引用"
    )


# ────────────────────────────────────────────────────────────────────
# §7  AnalysisEvent
# ────────────────────────────────────────────────────────────────────


class Evidence(_EventBase):
    """证据元素。"""

    family: EvidenceFamily = Field(...)
    type: str = Field(..., description="如 volume_z, taker_delta, oi_expansion")
    window: str | None = Field(default=None)
    value: float | None = Field(default=None)
    reference: dict[str, Any] | None = Field(default=None)
    threshold: float | None = Field(default=None)
    passed: bool = Field(...)
    source: str | None = Field(default=None)


class Veto(_EventBase):
    """Veto（否决）元素。"""

    type: VetoType = Field(...)
    triggered: bool = Field(...)
    severity: VetoSeverity = Field(...)
    detail: dict[str, Any] | None = Field(default=None)


class AnalysisEvent(_EventBase):
    """检测器 + 状态机输出。每次状态变化产生一条。

    LLM 只读 AnalysisEvent 生成自然语言，不得覆盖 new_state。
    """

    symbol: str = Field(..., min_length=1)
    direction: Direction | None = Field(default=None)
    previous_state: State = Field(...)
    new_state: State = Field(...)
    evidence: list[Evidence] = Field(default_factory=list)
    vetoes: list[Veto] = Field(default_factory=list)
    asof: int = Field(..., ge=0)
    confidence_state: ConfidenceState = Field(...)
