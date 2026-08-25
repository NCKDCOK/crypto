"""Recovery Manager + SystemMode 测试 — V1.2 停机恢复。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.config import AppConfigBundle, RecoveryConfig
from src.domain import SystemMode
from src.recovery.manager import RecoveryManager, RecoveryReport
from src.runtime import MarketRadarRuntime
from src.clock import TestClock
from src.storage import InMemoryRepository


class TestRecoveryTier:
    def test_quick(self):
        cfg = RecoveryConfig()
        assert RecoveryManager.tier(60.0, cfg) == "quick"

    def test_full(self):
        cfg = RecoveryConfig()
        assert RecoveryManager.tier(600.0, cfg) == "full"

    def test_rebuild(self):
        cfg = RecoveryConfig()
        assert RecoveryManager.tier(7200.0, cfg) == "rebuild"


class TestFreshStart:
    async def test_no_history_returns_warmup(self):
        """首次启动（无历史）→ WARMUP，不发 REST。"""
        cfg = AppConfigBundle()
        repo = MagicMock()
        repo.get_last_write_ms.return_value = None
        fe = MagicMock()
        rm = RecoveryManager(repo, cfg.recovery, TestClock(0), MagicMock(), "https://x", fe)
        report = await rm.recover(["BTCUSDT"], ["1m"], 1000)
        assert report.fresh_start is True
        assert report.mode == SystemMode.WARMUP
        assert report.klines_backfilled == 0


class TestSystemModeGating:
    """非 LIVE 模式不产出强确认 Top10 / 不发正式推送。"""

    def _rt(self):
        cfg = AppConfigBundle()
        return MarketRadarRuntime(cfg, clock=TestClock(0), repository=InMemoryRepository())

    def test_default_warmup_top10_empty(self):
        rt = self._rt()
        assert rt.system_mode == SystemMode.WARMUP
        assert rt.is_live is False
        assert rt.get_top10() == []
        summary = rt.get_market_summary()
        assert summary["top10"] == []
        assert "恢复" in summary["conclusion"] or "预热" in summary["conclusion"]

    def test_live_top10_produced(self):
        rt = self._rt()
        rt.system_mode = SystemMode.LIVE
        assert rt.is_live is True
        # 无数据时 top10 仍为空（无符合条件的 symbol），但不再被 mode 拦截
        assert rt.get_top10() == []

    def test_mode_promotion_to_live(self):
        rt = self._rt()
        rt.system_mode = SystemMode.WARMUP
        # 模拟样本达标
        fe_state = rt.feature_engine.get_state("BTCUSDT")
        fe_state.baseline_volumes = list(range(rt.cfg.recovery.live_min_samples + 5))
        rt.deep_scanner.symbols = ["BTCUSDT"]
        rt._promote_mode(0)
        assert rt.system_mode == SystemMode.LIVE

    def test_recovery_to_warmup_then_live(self):
        rt = self._rt()
        rt.system_mode = SystemMode.RECOVERY
        fe_state = rt.feature_engine.get_state("BTCUSDT")
        fe_state.baseline_volumes = [1.0]  # 有样本但不足
        rt.deep_scanner.symbols = ["BTCUSDT"]
        rt._promote_mode(0)
        assert rt.system_mode == SystemMode.WARMUP  # RECOVERY→WARMUP（样本>0 但不足 LIVE）
        # 再加样本
        fe_state.baseline_volumes = list(range(rt.cfg.recovery.live_min_samples + 1))
        rt._promote_mode(0)
        assert rt.system_mode == SystemMode.LIVE
