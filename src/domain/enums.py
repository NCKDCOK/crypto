"""全局枚举 — 先于所有事件对象定义。

依据：docs/DATA_MODEL.md §0
"""

from __future__ import annotations

from enum import Enum


class AggressorSide(str, Enum):
    """主动成交方向，从 Binance aggTrade 的 ``m`` 字段推导。

    P0 约束：Binance aggTrade 的 ``m`` 表示**买方是否为 maker**。
    - ``m == True``  → 买方是 maker → 卖方主动 → ``SELL``
    - ``m == False`` → 买方不是 maker → 买方主动 → ``BUY``

    此映射若写反，CVD / Taker Delta 全量反转。
    """

    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_binance_m(cls, m: bool | None) -> AggressorSide:
        """从 Binance aggTrade ``m`` 字段推导 aggressor_side。

        ``m`` 含义：买方是否为 maker。
        - ``m=True``  → 买方是 maker → 卖方是 taker → SELL
        - ``m=False`` → 买方是 taker → BUY
        - ``m=None``  → 无法判定 → UNKNOWN
        """
        if m is None:
            return cls.UNKNOWN
        return cls.SELL if m else cls.BUY


class Direction(str, Enum):
    """资金行为方向推断。"""

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class ConfidenceState(str, Enum):
    """置信上下文，由 Data Health 派生，决定状态能否升级。

    - CONFIDENT：所有关键输入 OK → 允许全部状态
    - DEGRADED：存在 WARN，无 STALE/DRIFT/FAIL → 最高 SUSPECTED_START
    - UNKNOWN：关键输入 STALE/DRIFT/FAIL → 禁止任何 CONFIRMED
    """

    CONFIDENT = "CONFIDENT"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class HealthLevel(str, Enum):
    """单个数据流的健康状态。"""

    OK = "OK"
    WARN = "WARN"
    STALE = "STALE"
    DRIFT = "DRIFT"
    FAIL = "FAIL"


class KlineInterval(str, Enum):
    """K 线周期。V1 确认源仅 ``1m``。"""

    S1 = "1s"
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H8 = "8h"
    H12 = "12h"
    D1 = "1d"
    D3 = "3d"
    W1 = "1w"
    M_1 = "1M"  # noqa: N815 — 月份，避免与 M1(1分钟) 冲突


class State(str, Enum):
    """状态机状态。依据 docs/STATE_MACHINE.md §0。"""

    SLEEPING = "SLEEPING"
    ANOMALY = "ANOMALY"
    SUSPECTED_START = "SUSPECTED_START"
    START_CONFIRMED = "START_CONFIRMED"
    CONTINUATION = "CONTINUATION"
    EXHAUSTION = "EXHAUSTION"
    WITHDRAWAL = "WITHDRAWAL"
    REJECTED = "REJECTED"
    COOLDOWN = "COOLDOWN"


class EvidenceFamily(str, Enum):
    """证据族。"""

    ANOMALY = "ANOMALY"
    FLOW = "FLOW"
    POSITION = "POSITION"
    PRICE_EFFECT = "PRICE_EFFECT"
    CONTEXT = "CONTEXT"


class VetoType(str, Enum):
    """Veto 类型。依据 docs/ANALYSIS_MODEL.md §4 + 改造任务文档 §16。"""

    DATA_STALE = "data_stale"
    RAPID_RETRACE = "rapid_retrace"
    OI_CONTRACTION = "oi_contraction"
    DELTA_REVERSAL = "delta_reversal"
    NO_ACCEPTANCE = "no_acceptance"
    LOW_EFFICIENCY_ABSORPTION = "low_efficiency_absorption"
    CROWDING_EXTREME = "crowding_extreme"
    ONE_BAR_SPIKE = "one_bar_spike"


class VetoSeverity(str, Enum):
    """Veto 严重度。"""

    HARD = "hard"
    SOFT = "soft"
