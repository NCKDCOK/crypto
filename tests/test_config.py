"""Config 加载与 schema 校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import AppConfigBundle, load_config


class TestConfigDefaults:
    def test_default_bundle(self):
        bundle = AppConfigBundle()
        assert bundle.app.exchange == "binance"
        assert bundle.app.ws_base_url == "wss://fstream.binance.com"
        assert bundle.data_health.freshness.aggtrade_active_ms == 5000
        assert bundle.data_health.freshness.oi_lookup_tolerance_ms == 15000
        assert bundle.features.baseline_window == "1h"
        assert bundle.features.epsilon == 1.0
        assert bundle.state_machine.cooldown_seconds == 300
        assert bundle.detectors.anomaly_volume_z == 3.0


class TestLoadConfig:
    def test_load_from_configs_dir(self):
        configs_dir = Path(__file__).parent.parent / "configs"
        bundle = load_config(configs_dir)
        assert bundle.app.exchange == "binance"
        assert bundle.app.ws_base_url == "wss://fstream.binance.com"
        assert bundle.data_health.freshness.aggtrade_active_ms == 5000
        assert bundle.data_health.rate_limiter.weight_limit_per_minute == 1200
        assert bundle.features.baseline_window == "1h"
        assert bundle.state_machine.cooldown_seconds == 300
        assert bundle.symbols.quote_asset == "USDT"
        assert bundle.detectors.anomaly_volume_z == 3.0

    def test_missing_dir_uses_defaults(self):
        bundle = load_config(Path("/nonexistent"))
        assert bundle.app.exchange == "binance"
        assert bundle.state_machine.cooldown_seconds == 300
