"""应用主入口 — 资金行为雷达 Dashboard。

串联 runtime（两阶段 Radar + Health + Feature + Detector + StateMachine）。
依据：改造任务文档 §20-§24

用法：
  uvicorn src.main:app --host 127.0.0.1 --port 8050
  浏览器访问 http://127.0.0.1:8050/
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.config import load_config
from src.runtime import MarketRadarRuntime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

runtime: MarketRadarRuntime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runtime
    cfg = load_config(CONFIGS_DIR)
    runtime = MarketRadarRuntime(cfg)
    await runtime.start()
    logger.info("runtime started, universe=%d", len(runtime.universe.universe))
    yield
    await runtime.stop()


app = FastAPI(
    title="资金行为雷达",
    description="资金行为驱动的实时行情分析雷达",
    lifespan=lifespan,
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """首页 — 返回 SPA shell。"""
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/radar")
async def get_radar():
    if runtime is None:
        return []
    return runtime.get_radar()


@app.get("/api/stats")
async def get_stats():
    if runtime is None:
        return {}
    return runtime.get_stats()


@app.get("/api/health")
async def get_health():
    if runtime is None:
        return []
    return runtime.get_health()


@app.get("/api/health/coverage")
async def get_health_coverage():
    """数据健康覆盖率（V1.3 §46）— 首页"数据健康 92%" 的数据源。

    独立于 /api/health 的明细行（app.js State.healthData 保持数组协议不变）。
    """
    if runtime is None:
        return {
            "coverage_pct": 0.0,
            "healthy_pairs": 0,
            "total_pairs": 0,
            "level": "anomaly",
            "level_label": "异常",
            "critical_stream_down": False,
            "per_stream": {},
        }
    return runtime.get_health_coverage()


@app.get("/api/symbol/{symbol}")
async def get_symbol_detail(symbol: str):
    if runtime is None:
        return {"error": "not ready"}
    return runtime.get_symbol_detail(symbol) or {"symbol": symbol, "state": "NO_DATA"}


@app.get("/api/signals")
async def get_signals():
    if runtime is None:
        return []
    return runtime.get_signal_history()


@app.get("/api/top10")
async def get_top10():
    """Top10 排名 — 按 RankingScore 排序。"""
    if runtime is None:
        return []
    return runtime.get_top10()


@app.get("/api/market-summary")
async def get_market_summary():
    """市场总览 — 系统结论 + Top10 + 统计。"""
    if runtime is None:
        return {"conclusion": "系统启动中...", "top10": [], "state_counts": {}}
    return runtime.get_market_summary()


@app.get("/api/prices")
async def get_prices():
    """轻量价格快照（前端 1-2s 轮询当前价，V1.2 §6.1）。"""
    if runtime is None:
        return {}
    return runtime.get_prices()


@app.get("/api/pushes")
async def get_pushes():
    """V1.2 §37 推送历史（State Transition Push）。"""
    if runtime is None:
        return []
    return runtime.push_history[-50:]
