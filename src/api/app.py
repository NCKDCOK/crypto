"""FastAPI 应用定义。

[DEPRECATED] V1.1 统一到 runtime data model。
实际运行入口为 src.main，直接使用 MarketRadarRuntime。
此模块的 create_app + DashboardService 已不再被 main.py 使用。

依据：epic-09 Task 09-A, V1.1 P0.6
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query

from src.api.dashboard import DashboardService
from src.domain import AnalysisEvent, State


def create_app(dashboard: DashboardService | None = None) -> FastAPI:
    """创建 FastAPI 应用。"""
    app = FastAPI(
        title="资金行为驱动行情分析系统",
        description="只读公开市场数据，只做分析提醒，不自动交易",
        version="0.1.0",
    )
    dash = dashboard or DashboardService()
    app.state.dashboard = dash

    @app.get("/api/radar")
    async def get_radar() -> list[dict[str, Any]]:
        """Market Radar — 全市场状态概览。"""
        return dash.get_market_radar()

    @app.get("/api/symbol/{symbol}")
    async def get_symbol_detail(symbol: str) -> dict[str, Any]:
        """Symbol Detail — 单 symbol 证据链详情。"""
        detail = dash.get_symbol_detail(symbol)
        if detail is None:
            raise HTTPException(status_code=404, detail="symbol not found")
        return detail

    @app.get("/api/signals")
    async def get_signals(
        symbol: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        """Signal History — 历史信号。"""
        return dash.get_signal_history(symbol, limit)

    @app.get("/api/health")
    async def health_check() -> dict[str, str]:
        """健康检查。"""
        return {"status": "ok"}

    return app
