"""RecommendationLifecycleEngine — Published Recommendation 发布后生命周期管理（V1.4 §六）。

Supervisor 真正接管已发布推荐：发布 → 注册 → 持续监督 → 状态转移 → 退出。

状态机（§二 RecommendationStatus，首页活跃区 = PUBLISHED/MONITORING/WEAKENING/RISK）：

    PUBLISHED → MONITORING ⇄ WEAKENING → RISK → EXITED/INVALIDATED/EXPIRED

立即退出（无视滞回 / 驻留，§四.4 + §八 + §三十三例外）：
    Hard Veto     → INVALIDATED (HARD_VETO)
    Withdrawal    → EXITED (SIGNAL_WITHDRAWAL)
    Invalidation  → INVALIDATED (INVALIDATION_HIT)   [价格触及失效位 / 结构失效]
    Data Critical → EXITED (DATA_CRITICAL)           [核心数据源断线 / 严重 stale]

普通降级（§八：连续 N 次离开正式范围才退出；§三十三：minimum_published_lifetime 内不退出）：
    state 离开正式范围（非 Withdrawal）→ fail_streak++；
        驻留期满且 streak >= lifecycle_downgrade_streak → EXITED (STATE_EXIT)
        否则 → WEAKENING（继续监督，不删除）

风险池（§七.6）：EXHAUSTION / DISTRIBUTION → RISK（仍活跃，首页风险提醒）。

条件减弱（§三十三：score 抖动不让推荐消失）：
    仍在正式范围但 Opportunity 跌破门禁门槛 → WEAKENING（保持活跃，仅标签变化）。

正常：→ MONITORING（重置 streak）。

设计要点：
- published_* 发布时冻结，绝不回写（§二十七）；本引擎只更新 current_*。
- 「决策窗口」由调用方（runtime）节奏驱动；本引擎按 tick 计连续失败，
  并叠加 minimum_published_lifetime 驻留保护，等价于「持续失败超过一个 5m 周期才普通降级」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.config import RecommendationConfig
from src.recommendations.models import (
    EXIT_REASON_DATA_CRITICAL,
    EXIT_REASON_HARD_VETO,
    EXIT_REASON_INVALIDATION_HIT,
    EXIT_REASON_STATE_EXIT,
    EXIT_REASON_WITHDRAWAL,
    PublishedRecommendation,
    RecommendationStatus,
)


@dataclass(frozen=True)
class LifecycleContext:
    """单 tick 生命周期评估输入（runtime 组装）。"""

    now_ms: int
    current_price: float | None
    current_state: str
    current_opportunity_score: float | None
    current_signal_confirmation: float | None
    current_data_confidence: float | None
    # 立即退出信号（§四.4 / §八 / §三十三例外）
    hard_veto: bool = False
    withdrawal_active: bool = False
    invalidated: bool = False        # 价格触及失效位 / 结构失效 / Setup 失效
    data_critical: bool = False     # 核心数据源断线 / data_confidence 严重低
    # 风险池（§七.6）：非 None → RISK 状态（仍活跃）
    risk_status: str | None = None  # EXHAUSTION / DISTRIBUTION / ...
    # 是否仍在正式范围（START_CONFIRMED / CONTINUATION）
    in_formal_range: bool = True


@dataclass
class LifecycleDecision:
    """一次生命周期评估的决策输出。"""

    recommendation_id: str
    new_status: RecommendationStatus | None = None  # None = 状态不变
    exit_reason: str | None = None
    transitioned: bool = False        # 本 tick 是否发生状态转移
    exited: bool = False             # 是否进入终态
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "new_status": self.new_status.value if self.new_status else None,
            "exit_reason": self.exit_reason,
            "transitioned": self.transitioned,
            "exited": self.exited,
            "reason": self.reason,
        }


@dataclass
class _Track:
    """每条推荐的滞回追踪（§八）。"""

    rec_id: str
    registered_at: int
    fail_streak: int = 0
    weaken_streak: int = 0


class RecommendationLifecycleEngine:
    """Published Recommendation 生命周期管理器（§六）。

    引擎就地更新 rec.current_* 与 status / closed_at / exit_reason；
    持久化由调用方（runtime → repository.save）负责。
    """

    def __init__(self, config: RecommendationConfig) -> None:
        self.cfg = config
        self._tracks: dict[str, _Track] = {}

    # ── 注册 / 清理 ──────────────────────────────────────────────────

    def register(self, rec: PublishedRecommendation, now_ms: int) -> None:
        """发布时注册一条推荐（幂等）。"""
        if rec.recommendation_id not in self._tracks:
            self._tracks[rec.recommendation_id] = _Track(
                rec_id=rec.recommendation_id, registered_at=now_ms,
            )

    def forget(self, rec_id: str) -> None:
        """清除一条推荐的滞回追踪（终态归档后可调用）。"""
        self._tracks.pop(rec_id, None)

    def get_track(self, rec_id: str) -> _Track | None:
        return self._tracks.get(rec_id)

    # ── 评估 ─────────────────────────────────────────────────────────

    def tick_fast(
        self, rec: PublishedRecommendation, ctx: LifecycleContext,
    ) -> LifecycleDecision:
        """FAST PATH（实时每 tick）：只处理即时退出 + current_* 更新。

        普通状态转移（减弱/降级/风险/恢复）留给 tick_slow（5m 收盘边界）。
        即时退出（§四.4/§八/§三十三例外）：Hard Veto / Withdrawal /
        Invalidation / Data Critical —— 无视滞回与驻留，立即终态。
        """
        track = self._tracks.get(rec.recommendation_id)
        if track is None:
            self.register(rec, ctx.now_ms)
            track = self._tracks[rec.recommendation_id]

        if rec.is_terminal():
            return LifecycleDecision(rec.recommendation_id, reason="已终态，跳过")

        # ── 持续更新 current_*（绝不回写 published_*，§二十七）──
        rec.current_price = ctx.current_price
        rec.current_state = ctx.current_state
        rec.current_opportunity_score = ctx.current_opportunity_score
        rec.current_signal_confirmation = ctx.current_signal_confirmation
        rec.current_data_confidence = ctx.current_data_confidence
        rec.updated_at = ctx.now_ms

        if ctx.hard_veto:
            return self._exit(rec, track, RecommendationStatus.INVALIDATED,
                              EXIT_REASON_HARD_VETO,
                              "Hard Veto 即时失效", ctx.now_ms)
        if ctx.withdrawal_active:
            return self._exit(rec, track, RecommendationStatus.EXITED,
                              EXIT_REASON_WITHDRAWAL,
                              "资金撤离即时退出", ctx.now_ms)
        if ctx.invalidated:
            return self._exit(rec, track, RecommendationStatus.INVALIDATED,
                              EXIT_REASON_INVALIDATION_HIT,
                              "价格/结构失效即时退出", ctx.now_ms)
        if ctx.data_critical:
            return self._exit(rec, track, RecommendationStatus.EXITED,
                              EXIT_REASON_DATA_CRITICAL,
                              "核心数据严重异常即时退出", ctx.now_ms)
        return LifecycleDecision(rec.recommendation_id,
                                  reason="实时监督（状态转移在 5m 收盘边界）")

    def tick_slow(
        self, rec: PublishedRecommendation, ctx: LifecycleContext,
    ) -> LifecycleDecision:
        """SLOW PATH（仅 5m 收盘决策窗口）：正常→减弱 / 减弱→恢复 / 减弱→退出 /
        Setup 是否仍成立 / 风险池。

        fail_streak 按 **5m Decision Window** 计数（连续 N 个 5m 收盘失败才退出），
        而非 2 秒 tick —— 避免驻留期满后 4 秒就退出（§三十三/§八）。
        含即时退出防御（若 fast 未先跑）。
        """
        track = self._tracks.get(rec.recommendation_id)
        if track is None:
            self.register(rec, ctx.now_ms)
            track = self._tracks[rec.recommendation_id]

        if rec.is_terminal():
            return LifecycleDecision(rec.recommendation_id, reason="已终态，跳过")

        rec.current_price = ctx.current_price
        rec.current_state = ctx.current_state
        rec.current_opportunity_score = ctx.current_opportunity_score
        rec.current_signal_confirmation = ctx.current_signal_confirmation
        rec.current_data_confidence = ctx.current_data_confidence
        rec.updated_at = ctx.now_ms

        # 即时退出防御（正常 fast 已处理）
        if ctx.hard_veto:
            return self._exit(rec, track, RecommendationStatus.INVALIDATED,
                              EXIT_REASON_HARD_VETO, "Hard Veto 即时失效", ctx.now_ms)
        if ctx.withdrawal_active:
            return self._exit(rec, track, RecommendationStatus.EXITED,
                              EXIT_REASON_WITHDRAWAL, "资金撤离即时退出", ctx.now_ms)
        if ctx.invalidated:
            return self._exit(rec, track, RecommendationStatus.INVALIDATED,
                              EXIT_REASON_INVALIDATION_HIT,
                              "价格/结构失效即时退出", ctx.now_ms)
        if ctx.data_critical:
            return self._exit(rec, track, RecommendationStatus.EXITED,
                              EXIT_REASON_DATA_CRITICAL,
                              "核心数据严重异常即时退出", ctx.now_ms)

        min_life_ms = int(self.cfg.minimum_published_lifetime_s * 1000)
        in_lifetime = (ctx.now_ms - rec.published_at) < min_life_ms
        threshold = self.cfg.lifecycle_downgrade_streak

        # ── 风险池（§七.6）：EXHAUSTION / DISTRIBUTION → RISK（仍活跃）──
        if ctx.risk_status is not None:
            return self._set(rec, track, RecommendationStatus.RISK,
                             risk_status=ctx.risk_status,
                             reason=f"进入风险池（{ctx.risk_status}），继续监督")

        # ── 普通降级：state 离开正式范围（非 Withdrawal）── §八 ──
        # fail_streak 按 5m Decision Window 计数（每次 tick_slow = 1 个窗口）
        if not ctx.in_formal_range:
            track.fail_streak += 1
            if not in_lifetime and track.fail_streak >= threshold:
                return self._exit(
                    rec, track, RecommendationStatus.EXITED, EXIT_REASON_STATE_EXIT,
                    f"连续 {track.fail_streak} 个 5m 决策窗口离开正式范围，普通降级退出",
                    ctx.now_ms,
                )
            return self._set(
                rec, track, RecommendationStatus.WEAKENING,
                reason=(
                    f"离开正式范围（{track.fail_streak}/{threshold} 5m 窗口），"
                    f"{'驻留保护' if in_lifetime else '滞回保护'}中继续监督"
                ),
            )

        # ── 条件减弱：score 抖动但不删除（§三十三）──
        opp = ctx.current_opportunity_score
        if opp is not None and opp < self.cfg.min_opportunity:
            track.weaken_streak += 1
            return self._set(
                rec, track, RecommendationStatus.WEAKENING,
                reason="条件减弱（Opportunity 跌破门禁门槛），继续监督不删除",
            )

        # ── 正常 ──
        track.fail_streak = 0
        track.weaken_streak = 0
        return self._set(rec, track, RecommendationStatus.MONITORING,
                         reason="核心条件满足，正常监督")

    # ── helpers ──────────────────────────────────────────────────────

    def _set(
        self, rec: PublishedRecommendation, track: _Track,
        status: RecommendationStatus, *, risk_status: str | None = None,
        reason: str,
    ) -> LifecycleDecision:
        transitioned = rec.status != status
        rec.status = status
        if risk_status is not None:
            rec.risk_status = risk_status
        elif status != RecommendationStatus.RISK:
            # 离开 RISK 时清空 risk_status（MONITORING/WEAKENING 无风险标签）
            rec.risk_status = None
        return LifecycleDecision(
            recommendation_id=rec.recommendation_id,
            new_status=status if transitioned else None,
            transitioned=transitioned,
            reason=reason,
        )

    def _exit(
        self, rec: PublishedRecommendation, track: _Track,
        status: RecommendationStatus, exit_reason: str, reason: str,
        now_ms: int,
    ) -> LifecycleDecision:
        transitioned = rec.status != status
        rec.status = status
        rec.exit_reason = exit_reason
        rec.closed_at = now_ms
        rec.risk_status = None
        track.fail_streak = 0
        track.weaken_streak = 0
        return LifecycleDecision(
            recommendation_id=rec.recommendation_id,
            new_status=status if transitioned else None,
            exit_reason=exit_reason,
            transitioned=transitioned,
            exited=True,
            reason=reason,
        )
