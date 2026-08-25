"""Recovery Manager — 停机恢复（V1.2 用户追加需求）。

启动流程：
  启动 → 读取本地最后写入时间 → 计算停机时长 → 三档策略 → 补缺失 K 线
  → 重建趋势/结构（加载历史 K 线到 FeatureEngine）→ 清空过期实时状态
  → 重新建立 OI/CVD/Delta 基线 → 预热 → LIVE

三档恢复策略：
  <5min      → quick：补小缺口，快速恢复
  5min~1h    → full：补历史 K 线 + 重算短周期状态
  >1h        → rebuild：旧实时状态全部失效（state machine 内存态本就重置），
              旧 Trade Plan 标 EXPIRED，完整重建

关键不变量：
- OI 重启后第一条数据只作新基准（FeatureEngine 内存 OI 不加载历史，避免误判"突然增仓"）。
- CVD/Delta/Price Efficiency 重新预热（warmup_min_samples 控制）。
- RECOVERY/WARMUP 期：可展示数据，不发正式推送、不进入强确认 Top10。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.domain import KlineInterval, SystemMode

if TYPE_CHECKING:
    from src.clock import Clock
    from src.config import RecoveryConfig
    from src.features.engine import FeatureEngine
    from src.health.rate_limiter import RateLimiter
    from src.storage.sqlite_repository import SqliteRepository

logger = logging.getLogger(__name__)


@dataclass
class RecoveryReport:
    """恢复结果。"""

    mode: SystemMode = SystemMode.WARMUP
    fresh_start: bool = True  # 无历史数据（首次启动）
    downtime_s: float = 0.0
    tier: str = "none"  # quick / full / rebuild / none
    klines_backfilled: int = 0
    klines_loaded: int = 0
    trade_plans_expired: int = 0
    notes: list[str] = field(default_factory=list)


class RecoveryManager:
    """停机恢复管理器。"""

    def __init__(
        self,
        repo: SqliteRepository,
        cfg: RecoveryConfig,
        clock: Clock,
        rate_limiter: RateLimiter,
        rest_base_url: str,
        feature_engine: FeatureEngine,
    ) -> None:
        self.repo = repo
        self.cfg = cfg
        self.clock = clock
        self.rate_limiter = rate_limiter
        self.rest_base_url = rest_base_url.rstrip("/")
        self.feature_engine = feature_engine

    @staticmethod
    def tier(downtime_s: float, cfg: RecoveryConfig) -> str:
        if downtime_s < cfg.tier_quick_s:
            return "quick"
        if downtime_s < cfg.tier_full_s:
            return "full"
        return "rebuild"

    async def recover(
        self,
        symbols: list[str],
        intervals: list[str],
        now_ms: int,
    ) -> RecoveryReport:
        """执行恢复流程，返回 RecoveryReport。"""
        report = RecoveryReport()
        last_write = self.repo.get_last_write_ms()

        if last_write is None:
            # 首次启动 — 无历史
            report.fresh_start = True
            report.mode = SystemMode.WARMUP
            report.notes.append("首次启动，无历史数据，进入 WARMUP")
            logger.info("recovery_fresh_start → WARMUP")
            return report

        downtime_s = max(0.0, (now_ms - last_write) / 1000.0)
        report.fresh_start = False
        report.downtime_s = downtime_s
        report.tier = self.tier(downtime_s, self.cfg)
        logger.info("recovery_downtime=%.1fs tier=%s", downtime_s, report.tier)

        # 1. 补缺失 K 线（REST backfill）
        backfilled = await self._backfill_klines(symbols, intervals, now_ms)
        report.klines_backfilled = backfilled

        # 2. 加载历史 K 线到 FeatureEngine（供 Structure/VP 用）
        loaded = self._load_kline_history(symbols, intervals)
        report.klines_loaded = loaded

        # 3. Trade Plan 过期（停机超过阈值 → 旧 plan 标 EXPIRED）
        if downtime_s > self.cfg.trade_plan_expire_s:
            expired = self.repo.expire_trade_plans(None, now_ms)
            report.trade_plans_expired = expired
            if expired:
                report.notes.append(f"停机 {downtime_s:.0f}s 超过阈值，{expired} 份旧 Trade Plan 标记 EXPIRED")

        # 4. 模式：补完历史 → RECOVERY（仍需预热）→ 由 runtime 在样本达标后升 LIVE
        report.mode = SystemMode.RECOVERY
        report.notes.append(f"tier={report.tier}，历史已补 {backfilled} 根 K 线，加载 {loaded} 根")
        logger.info("recovery_done tier=%s backfilled=%d loaded=%d expired=%d → RECOVERY",
                    report.tier, backfilled, loaded, report.trade_plans_expired)
        return report

    async def _backfill_klines(
        self, symbols: list[str], intervals: list[str], now_ms: int,
    ) -> int:
        """REST 拉取每个 symbol+interval 缺失的 K 线并持久化（限速，不阻塞 server）。"""
        total = 0
        client = await self.rate_limiter._get_client()
        for sym in symbols:
            for iv in intervals:
                latest = self.repo.get_latest_kline_open_time(sym, iv)
                # 间隔毫秒
                iv_ms = _interval_ms(iv)
                if latest is not None and (now_ms - latest) < iv_ms:
                    # 缺口不足一根，跳过
                    continue
                start = latest + iv_ms if latest is not None else (now_ms - iv_ms * self.cfg.backfill_kline_limit)
                limit = self.cfg.backfill_kline_limit
                try:
                    url = (f"{self.rest_base_url}/fapi/v1/klines?symbol={sym}"
                           f"&interval={iv}&startTime={int(start)}&limit={limit}")
                    r = await client.get(url, timeout=15)
                    if r.status_code != 200:
                        logger.warning("backfill_kline_http %s %s status=%s", sym, iv, r.status_code)
                        continue
                    rows = r.json()
                    if not isinstance(rows, list):
                        continue
                    from decimal import Decimal
                    from src.domain import KlineEvent
                    for row in rows:
                        # Binance klines 数组: [open_time, open, high, low, close, volume,
                        # close_time, quote_volume, trade_count, ...]
                        kl = KlineEvent(
                            symbol=sym,
                            interval=KlineInterval(iv),
                            open_time=int(row[0]),
                            close_time=int(row[6]),
                            event_time=int(row[0]),
                            receive_time=now_ms,
                            open=Decimal(str(row[1])),
                            high=Decimal(str(row[2])),
                            low=Decimal(str(row[3])),
                            close=Decimal(str(row[4])),
                            volume=Decimal(str(row[5])),
                            quote_volume=Decimal(str(row[7])),
                            trade_count=int(row[8]),
                            is_closed=True,
                        )
                        self.repo.save_kline(kl)
                        total += 1
                    # 每个请求后让出事件循环（不阻塞 server 响应）
                    await asyncio.sleep(0.05)
                except Exception:
                    logger.exception("backfill_kline_failed %s %s", sym, iv)
        return total

    def _load_kline_history(self, symbols: list[str], intervals: list[str]) -> int:
        """把持久化的历史 K 线加载到 FeatureEngine（供 Structure/VP）。"""
        total = 0
        for sym in symbols:
            for iv in intervals:
                bars = self.repo.get_recent_klines(sym, iv, limit=self.cfg.backfill_kline_limit)
                if bars:
                    self.feature_engine.load_kline_history(sym, iv, bars)
                    total += len(bars)
        return total


def _interval_ms(interval: str) -> int:
    """K 线周期 → 毫秒。"""
    if interval.endswith("s"):
        return int(float(interval[:-1]) * 1000)
    if interval.endswith("m"):
        return int(float(interval[:-1]) * 60_000)
    if interval.endswith("h"):
        return int(float(interval[:-1]) * 3_600_000)
    if interval.endswith("d"):
        return int(float(interval[:-1]) * 86_400_000)
    return 60_000
