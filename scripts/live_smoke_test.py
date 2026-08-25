#!/usr/bin/env python3
"""Live Smoke Test — 真实数据连通性冒烟测试。

依据：改造任务文档 §28
默认只测试 BTCUSDT / ETHUSDT / SOLUSDT，运行 10 分钟。
输出：trade count / duplicate count / last event age / kline age / OI age /
      funding age / reconnect count / queue lag p50/p95 / Feature sample /
      state transitions，并打印 PASS / FAIL。

注意：这是 Smoke Test 的 3 个币，生产 Universe 不再硬编码 3 个币。

用法：
  python scripts/live_smoke_test.py [--duration 600] [--proxy http://127.0.0.1:7890]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
from pathlib import Path

# Windows 控制台 UTF-8 输出
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

# 让 src 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.runtime import (
    MarketRadarRuntime,
    STREAM_AGGTRADE,
    STREAM_FUNDING,
    STREAM_KLINE,
    STREAM_OI,
)

SMOKE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("smoke_test")


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = int((len(s) - 1) * p / 100.0)
    return s[k]


async def run_smoke(duration_s: int, proxy: str | None) -> dict:
    cfg = load_config(CONFIGS_DIR)
    # 仅 BTC/ETH/SOL
    cfg.symbols.whitelist = SMOKE_SYMBOLS
    cfg.symbols.top_n = 3
    cfg.symbols.max_symbols = 3
    cfg.symbols.liquidity_floor_usdt = 0
    cfg.app.deep_max_symbols = 3
    cfg.app.light_scan_interval_s = 30
    cfg.app.candidate_refresh_interval_s = 120
    if proxy:
        cfg.app.proxy = proxy

    rt = MarketRadarRuntime(cfg)
    await rt.start()
    logger.info("smoke test started, duration=%ds", duration_s)
    await asyncio.sleep(duration_s)

    now = rt.clock.now_ms()
    result: dict = {"symbols": SMOKE_SYMBOLS, "duration_s": duration_s}
    per_symbol: list[dict] = []

    dedup = rt.deep_scanner.dedup
    reconnect_agg = rt.deep_scanner._aggtrade.stats.reconnect_count if rt.deep_scanner._aggtrade else 0
    reconnect_kline = rt.deep_scanner._kline.stats.reconnect_count if rt.deep_scanner._kline else 0

    # queue lag p50/p95
    lag_values: list[float] = []
    for stream, lm in rt.queue_monitor.get_all_lag_metrics().items():
        lag_values.append(lm.receive_lag_ms)
    qm = rt.queue_monitor.get_queue_metrics("trade")

    for sym in SMOKE_SYMBOLS:
        st = rt.latest_state.get(sym)
        hs_agg = rt.watchdog.check_health(f"{STREAM_AGGTRADE}:{sym}")
        hs_kline = rt.watchdog.check_health(f"{STREAM_KLINE}:{sym}")
        hs_oi = rt.watchdog.check_health(f"{STREAM_OI}:{sym}")
        hs_funding = rt.watchdog.check_health(f"{STREAM_FUNDING}:{sym}")
        detail = rt.get_symbol_detail(sym)
        features = detail["features"] if detail else {}
        per_symbol.append({
            "symbol": sym,
            "trade_count": st.trade_count if st else 0,
            "aggtrade_age_ms": hs_agg.age_ms,
            "aggtrade_status": hs_agg.status.value,
            "kline_age_ms": hs_kline.age_ms,
            "kline_status": hs_kline.status.value,
            "oi_age_ms": hs_oi.age_ms,
            "oi_status": hs_oi.status.value,
            "funding_age_ms": hs_funding.age_ms,
            "funding_status": hs_funding.status.value,
            "confidence_state": rt.confidence.get(sym).value,
            "state": st.state.value if st else "NO_DATA",
            "feature_sample": {
                "cvd": features.get("cvd"),
                "taker_delta": features.get("taker_delta"),
                "oi_contracts": features.get("oi_contracts"),
                "oi_change_1m": features.get("oi_change_1m"),
                "funding": features.get("funding"),
                "price_return_30s": features.get("price_return_30s"),
                "price_efficiency": features.get("price_efficiency"),
                "volume_z": features.get("volume_z"),
            },
        })

    result["per_symbol"] = per_symbol
    result["duplicate_count"] = dedup.dropped_count if dedup else 0
    result["reconnect_count"] = {"aggTrade": reconnect_agg, "kline": reconnect_kline}
    result["queue_depth"] = qm.depth if qm else 0
    result["queue_max_depth"] = qm.max_depth if qm else 0
    result["queue_lag_p50_ms"] = percentile(lag_values, 50)
    result["queue_lag_p95_ms"] = percentile(lag_values, 95)
    result["transitions"] = len(rt.transition_history)
    result["rate_limiter"] = {
        "weight_used": rt.rate_limiter.state.weight_used,
        "total_429": rt.rate_limiter.state.total_429,
        "total_418": rt.rate_limiter.state.total_418,
        "circuit_open": rt.rate_limiter.state.circuit_open,
    }
    await rt.stop()
    return result


def evaluate(result: dict) -> tuple[bool, list[str]]:
    """评估 PASS / FAIL。"""
    failures: list[str] = []
    for ps in result["per_symbol"]:
        sym = ps["symbol"]
        # aggTrade 必须有数据且新鲜
        if ps["trade_count"] < 10:
            failures.append(f"{sym}: trade_count={ps['trade_count']} < 10")
        if ps["aggtrade_status"] in ("FAIL", "STALE"):
            failures.append(f"{sym}: aggTrade {ps['aggtrade_status']} (age={ps['aggtrade_age_ms']}ms)")
        # OI 必须有数据
        if ps["oi_status"] == "FAIL":
            failures.append(f"{sym}: OI FAIL (age={ps['oi_age_ms']}ms)")
        # funding 必须有数据
        if ps["funding_status"] == "FAIL":
            failures.append(f"{sym}: funding FAIL (age={ps['funding_age_ms']}ms)")
        # feature sample 必须有真实 OI/funding
        fs = ps["feature_sample"]
        if fs["oi_contracts"] is None:
            failures.append(f"{sym}: oi_contracts is None")
        if fs["funding"] is None:
            failures.append(f"{sym}: funding is None")
        if fs["cvd"] is None:
            failures.append(f"{sym}: cvd is None")
    # 重连次数不应过多
    if result["reconnect_count"]["aggTrade"] > 5:
        failures.append(f"aggTrade reconnect={result['reconnect_count']['aggTrade']} > 5")
    # 429/418 不应出现
    if result["rate_limiter"]["total_418"] > 0:
        failures.append(f"418 banned count={result['rate_limiter']['total_418']}")
    return (len(failures) == 0), failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Live smoke test")
    parser.add_argument("--duration", type=int, default=600, help="运行时长（秒），默认 600")
    parser.add_argument("--proxy", type=str, default=None, help="代理地址")
    args = parser.parse_args()

    result = asyncio.run(run_smoke(args.duration, args.proxy))
    print("\n" + "=" * 60)
    print("LIVE SMOKE TEST REPORT")
    print("=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    print("=" * 60)
    passed, failures = evaluate(result)
    if passed:
        print("RESULT: PASS")
        return 0
    else:
        print("RESULT: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
