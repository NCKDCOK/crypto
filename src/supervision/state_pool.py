"""State Pool Manager — 按当前生命周期把 symbol 分配到监督池（V1.3 §5–§6）。

池是**派生分组**：主 `State` 枚举保持不变（用户决策：不扩展 State，
池/标签只是派生分组）。State → 池的单射映射为主，派生标签可覆盖
（例如 setup_type=DISTRIBUTION 的 CONTINUATION → RISK 池）。

频率配置见 configs/supervision.yaml（SupervisionConfig），本模块不含阈值。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from src.domain import State


class PoolName(str, Enum):
    """监督池（V1.3 §6.1–§6.8）。"""

    NORMAL = "normal"
    ANOMALY = "anomaly"
    WATCH = "watch"
    CONFIRMED = "confirmed"
    CONTINUATION = "continuation"
    RISK = "risk"
    EXIT = "exit"
    ARCHIVE = "archive"


class SupervisionLevel(str, Enum):
    """监督强度（§7 supervision_level，由池派生）。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class PoolSpec:
    """单个监督池的元数据（§6 各池定义 + §8 state-aware 监督问题）。"""

    name: PoolName
    title: str
    states: tuple[State, ...]
    # 池内可出现的枚举外派生标签（§6 各池“包含”列表）
    labels: tuple[str, ...]
    # 频率区间（秒，§6）— 引擎实际使用 SupervisionConfig 的精确值
    interval_range_sec: tuple[float, float]
    # 该池的监督重点（§6 关注）
    focus: tuple[str, ...]
    # state-aware 监督问题（§8）
    supervision_question: str


# ── State → 池 单射（主映射，不扩展 State 枚举） ──────────────────────────
_STATE_TO_POOL: dict[State, PoolName] = {
    State.SLEEPING: PoolName.NORMAL,
    State.COOLDOWN: PoolName.NORMAL,
    State.ANOMALY: PoolName.ANOMALY,
    State.SUSPECTED_START: PoolName.WATCH,
    State.START_CONFIRMED: PoolName.CONFIRMED,
    State.CONTINUATION: PoolName.CONTINUATION,
    State.EXHAUSTION: PoolName.RISK,
    State.WITHDRAWAL: PoolName.EXIT,
    State.REJECTED: PoolName.ARCHIVE,
}

# ── 派生标签 → 池（覆盖 State 映射；标签来自行为引擎/setup_type 等） ──────
# 规范化小写标签：来源于 SetupTypeEngine/SymbolRuntimeState 的可判定标签。
_LABEL_TO_POOL: dict[str, PoolName] = {
    "dormant_revival": PoolName.ANOMALY,     # Dormant Revival 初期（§6.2）
    "accumulation": PoolName.WATCH,          # ACCUMULATION（§6.3）
    "retest_pending": PoolName.WATCH,        # RETEST_PENDING（§6.3）
    "breakout_candidate": PoolName.WATCH,    # Breakout Candidate（§6.3）
    "breakout_start": PoolName.WATCH,        # BREAKOUT_START（setup_type）
    "retest_reignition": PoolName.WATCH,     # RETEST_REIGNITION（setup_type）
    "distribution": PoolName.RISK,           # DISTRIBUTION（§6.6）
    "pump_risk": PoolName.RISK,              # 高顶部风险（§6.6）
    "high_top_risk": PoolName.RISK,          # 高顶部风险（§6.6）
    "high_withdrawal_risk": PoolName.RISK,   # 高撤离风险（§6.6）
    "direction_flip": PoolName.EXIT,         # DIRECTION_FLIP（§6.7）
    "invalidated": PoolName.EXIT,            # INVALIDATED（§6.7）
    "expired": PoolName.ARCHIVE,             # EXPIRED（§6.8）
    "closed": PoolName.ARCHIVE,              # CLOSED（§6.8）
}

# 标签优先级：同 tick 命中多标签时取更高优先级（更接近终局的池优先）
_LABEL_RANK: dict[PoolName, int] = {
    PoolName.NORMAL: 0,
    PoolName.ARCHIVE: 1,
    PoolName.ANOMALY: 2,
    PoolName.WATCH: 3,
    PoolName.CONTINUATION: 4,
    PoolName.CONFIRMED: 5,
    PoolName.RISK: 6,
    PoolName.EXIT: 7,
}

_POOL_LEVEL: dict[PoolName, SupervisionLevel] = {
    PoolName.NORMAL: SupervisionLevel.LOW,
    PoolName.ARCHIVE: SupervisionLevel.LOW,
    PoolName.ANOMALY: SupervisionLevel.MEDIUM,
    PoolName.CONTINUATION: SupervisionLevel.MEDIUM,
    PoolName.WATCH: SupervisionLevel.HIGH,
    PoolName.CONFIRMED: SupervisionLevel.HIGH,
    PoolName.RISK: SupervisionLevel.HIGH,
    PoolName.EXIT: SupervisionLevel.HIGH,
}

# 池 → 关闭顺序：决定标签多命中时的优先级（EXIT > RISK > CONFIRMED > ...）
_POOL_SPECS: tuple[PoolSpec, ...] = (
    PoolSpec(
        name=PoolName.NORMAL,
        title="普通扫描池",
        states=(State.SLEEPING, State.COOLDOWN),
        labels=(),
        interval_range_sec=(15.0, 30.0),
        focus=("短时成交额增量", "Trade Count 增量", "Price acceleration", "简单 OI 变化"),
        supervision_question="是否有新的异常出现？",
    ),
    PoolSpec(
        name=PoolName.ANOMALY,
        title="异动观察池",
        states=(State.ANOMALY,),
        labels=("dormant_revival",),
        interval_range_sec=(5.0, 10.0),
        focus=("Delta", "CVD", "OI", "Price Efficiency", "Rapid Retrace",
               "Spot/Perp", "Funding", "Pump Risk"),
        supervision_question="这次异常是真的吗？噪声、Pump，还是进入资金生命周期？",
    ),
    PoolSpec(
        name=PoolName.WATCH,
        title="重点观察池",
        states=(State.SUSPECTED_START,),
        labels=("accumulation", "retest_pending", "breakout_candidate",
                "breakout_start", "retest_reignition"),
        interval_range_sec=(1.0, 5.0),
        focus=("5m 收盘", "Breakout Hold", "Acceptance", "Retest", "Spot Confirmation",
               "OI persistence", "CVD persistence", "Second Impulse"),
        supervision_question="还缺哪些确认条件？",
    ),
    PoolSpec(
        name=PoolName.CONFIRMED,
        title="确认机会池",
        states=(State.START_CONFIRMED,),
        labels=(),
        interval_range_sec=(1.0, 5.0),
        focus=("Snapshot", "Trade Plan", "Simulation Queue", "正式 Push"),
        supervision_question="Trade Plan 是否成立？是否进入模拟观察？",
    ),
    PoolSpec(
        name=PoolName.CONTINUATION,
        title="趋势跟踪池",
        states=(State.CONTINUATION,),
        labels=(),
        interval_range_sec=(1.0, 5.0),
        focus=("OI persistence", "CVD persistence", "Taker persistence", "Retest health",
               "Second/Third impulse", "Distribution Risk", "Withdrawal Risk"),
        supervision_question="资金是否继续？",
    ),
    PoolSpec(
        name=PoolName.RISK,
        title="风险池",
        states=(State.EXHAUSTION,),
        labels=("distribution", "pump_risk", "high_top_risk", "high_withdrawal_risk"),
        interval_range_sec=(1.0, 5.0),
        focus=("CVD divergence", "OI decay", "Delta reversal", "High volume no progress",
               "Price Efficiency collapse", "Failed breakout", "Spot sell pressure"),
        supervision_question="是正常休息，还是派发/撤离？",
    ),
    PoolSpec(
        name=PoolName.EXIT,
        title="撤离池",
        states=(State.WITHDRAWAL,),
        labels=("direction_flip", "invalidated"),
        interval_range_sec=(1.0, 5.0),
        focus=("关闭 Setup", "结束模拟仓位", "写入结果", "进入归档"),
        supervision_question="Setup 是否正式结束？",
    ),
    PoolSpec(
        name=PoolName.ARCHIVE,
        title="历史归档",
        states=(State.REJECTED,),
        labels=("expired", "closed"),
        interval_range_sec=(30.0, 60.0),
        focus=("Replay", "Statistics", "Calibration"),
        supervision_question="（归档：仅用于回放/统计/校准）",
    ),
)


class StatePoolManager:
    """把 symbol 按 State（及派生标签）分配到监督池。

    - 不扩展主 State 枚举：池是派生分组。
    - `pool_for(state, labels)`：State 映射为主；传入的派生标签命中时覆盖
      （同 tick 多标签取最高优先级）。
    """

    def __init__(self) -> None:
        self._specs: dict[PoolName, PoolSpec] = {spec.name: spec for spec in _POOL_SPECS}

    # ── 查询 ──────────────────────────────────────────────────────────
    def pool_for(self, state: State, labels: Iterable[str] = ()) -> PoolName:
        """派生池：State 映射为主，派生标签覆盖（多标签取高优先级）。"""
        override: PoolName | None = None
        override_rank = -1
        for label in labels:
            pool = _LABEL_TO_POOL.get(label.lower())
            if pool is None:
                continue
            rank = _LABEL_RANK[pool]
            if rank > override_rank:
                override = pool
                override_rank = rank
        if override is not None:
            return override
        return _STATE_TO_POOL[state]

    def spec(self, pool: PoolName) -> PoolSpec:
        return self._specs[pool]

    def level_for(self, pool: PoolName) -> SupervisionLevel:
        return _POOL_LEVEL[pool]

    @property
    def pool_names(self) -> tuple[PoolName, ...]:
        return tuple(spec.name for spec in _POOL_SPECS)

    @property
    def specs(self) -> dict[PoolName, PoolSpec]:
        return dict(self._specs)