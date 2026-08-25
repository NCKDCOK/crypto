"""模拟验证枚举 — V1.3 §23 模拟队列状态机 + §31 退出原因。

依据：资金行为雷达_V1.3_..._更新计划.md §23 / §31。
"""

from __future__ import annotations

from enum import Enum


class SimulationStatus(str, Enum):
    """§23 模拟队列状态机。

    主线：
        WATCHING → ENTRY_ZONE_REACHED → REVALIDATING → ARMED
        → SIMULATED_ENTRY → OPEN → CLOSED
    旁路：
        EXPIRED / CANCELLED / INVALIDATED / MISSED

    - WATCHING：推荐已入队，等待价格进入参考 Entry Zone（§24 持续记录）
    - ENTRY_ZONE_REACHED：价格首次触及参考区间（§25 必须二次验证，不能直接成交）
    - REVALIDATING：入场二次验证执行中（§26 十项检查）
    - ARMED：验证通过，等待 Entry Zone 内第一笔符合价格（§27）
    - SIMULATED_ENTRY：模拟成交已记录（§28）
    - OPEN：模拟持仓中（§29 持续监督）
    - CLOSED：已平仓，结果已定稿（§31/§32）
    - EXPIRED：推荐超时未入场（§24 推荐过期时长）
    - CANCELLED：二次验证未通过（§27 记录原因）
    - INVALIDATED：观察期推荐状态失效（离开合法状态 / 明确 Veto）
    - MISSED：武装后宽限期满仍未成交
    """

    WATCHING = "WATCHING"
    ENTRY_ZONE_REACHED = "ENTRY_ZONE_REACHED"
    REVALIDATING = "REVALIDATING"
    ARMED = "ARMED"
    SIMULATED_ENTRY = "SIMULATED_ENTRY"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"
    MISSED = "MISSED"


class ExitReason(str, Enum):
    """§31 模拟退出原因（9 种）。"""

    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    TP3_HIT = "TP3_HIT"
    INVALIDATION_HIT = "INVALIDATION_HIT"
    SIGNAL_WITHDRAWAL = "SIGNAL_WITHDRAWAL"
    DISTRIBUTION_EXIT = "DISTRIBUTION_EXIT"
    DIRECTION_FLIP = "DIRECTION_FLIP"
    TIME_EXPIRED = "TIME_EXPIRED"
    MANUAL_CLOSE = "MANUAL_CLOSE"


# ── §32 B. Static Plan（固定 TP/Stop 执行）结局 ──
STATIC_OUTCOME_TP1 = "TP1_HIT"
STATIC_OUTCOME_STOP = "STOP_HIT"
STATIC_OUTCOME_INVALIDATED = "INVALIDATED"
STATIC_OUTCOME_TIME_EXPIRED = "TIME_EXPIRED"
STATIC_OUTCOME_TRACKING = "TRACKING"