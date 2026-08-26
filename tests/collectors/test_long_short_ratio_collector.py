"""LongShortRatioCollector 测试 — V1.4 §二十三（三个指标严格区分）."""

from __future__ import annotations

from src.collectors.long_short_ratio_collector import parse_long_short_ratio_response


class TestParseLongShortRatio:
    def test_parse_list_takes_latest(self):
        data = [
            {"symbol": "BTCUSDT", "longShortRatio": "1.20", "longAccount": "0.545", "shortAccount": "0.455", "timestamp": 1000},
            {"symbol": "BTCUSDT", "longShortRatio": "1.41", "longAccount": "0.585", "shortAccount": "0.415", "timestamp": 2000},
        ]
        r = parse_long_short_ratio_response(data, "global_account_ls_ratio", 3000)
        assert r == 1.41

    def test_parse_single_dict(self):
        r = parse_long_short_ratio_response(
            {"longShortRatio": "0.94"}, "top_trader_account_ls_ratio", 0)
        assert r == 0.94

    def test_parse_empty_list_none(self):
        assert parse_long_short_ratio_response([], "x", 0) is None

    def test_parse_missing_field_none(self):
        assert parse_long_short_ratio_response([{"symbol": "X"}], "x", 0) is None

    def test_parse_invalid_value_none(self):
        assert parse_long_short_ratio_response(
            [{"longShortRatio": "abc"}], "x", 0) is None
