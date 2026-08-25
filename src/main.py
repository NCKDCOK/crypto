"""应用主入口 — 实时数据 Dashboard。

串联流水线：
  aggTrade Collector (Binance WS)
  → 每 symbol 滚动窗口
  → Feature Engine
  → State Machine
  → DashboardService + AlertManager + Repository

用法：
  uvicorn src.main:app --host 127.0.0.1 --port 8050
  或 python -m uvicorn src.main:app --host 127.0.0.1 --port 8050

浏览器访问 http://127.0.0.1:8050/ 查看 Market Radar。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src.alerts.ai_summary import generate_summary
from src.alerts.manager import AlertManager
from src.api.dashboard import DashboardService
from src.clock import SystemClock
from src.collectors.aggtrade_collector import AggTradeCollector
from src.collectors.base_ws import WSStreamConfig
from src.domain import AnalysisEvent, TradeEvent
from src.features.engine import FeatureEngine
from src.state_machine.machine import StateMachine
from src.storage import InMemoryRepository
from src.windows.rolling_window import RollingWindow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

# 订阅的交易对（可调整）
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
# 特征计算周期（秒）
COMPUTE_INTERVAL_S = 2.0
# 窗口大小（毫秒）
WINDOW_MS = 30_000


class MarketDataPipeline:
    """实时数据流水线。"""

    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols
        self.clock = SystemClock()
        self.feature_engine = FeatureEngine()
        self.state_machine = StateMachine()
        self.dashboard = DashboardService(repository=InMemoryRepository())
        self.alerts = AlertManager()
        self.repository = InMemoryRepository()

        # 每 symbol 的滚动窗口
        self.windows: dict[str, RollingWindow] = {
            s: RollingWindow(WINDOW_MS) for s in symbols
        }
        self.received_count: dict[str, int] = {}

        self.collector = AggTradeCollector(
            symbols=symbols,
            clock=self.clock,
            on_trade=self._on_trade,
        )
        self._compute_task: asyncio.Task | None = None

    async def _on_trade(self, event: TradeEvent) -> None:
        """收到成交 → 加入滚动窗口。"""
        symbol = event.symbol
        if symbol in self.windows:
            self.windows[symbol].add(event.receive_time, event)
            self.received_count[symbol] = self.received_count.get(symbol, 0) + 1

    async def start(self) -> None:
        await self.collector.start()
        self._compute_task = asyncio.create_task(self._compute_loop())

    async def stop(self) -> None:
        if self._compute_task:
            self._compute_task.cancel()
        await self.collector.stop()

    async def _compute_loop(self) -> None:
        """周期计算特征 + 状态机。"""
        while True:
            try:
                now = self.clock.now_ms()
                for symbol in self.symbols:
                    window = self.windows[symbol]
                    trades = window.get_items(now)
                    if not trades:
                        continue

                    # 计算 FeatureSnapshot
                    snap = self.feature_engine.compute_snapshot(
                        symbol, trades, now, window_label="window"
                    )

                    # confidence 默认 CONFIDENT（演示）
                    self.state_machine.confidence._confidence[symbol] = (
                        __import__("src.domain", fromlist=["ConfidenceState"])
                        .ConfidenceState.CONFIDENT
                    )

                    # 状态机处理
                    event: AnalysisEvent | None = self.state_machine.process(snap, now)
                    if event is not None:
                        self.dashboard.update_event(event)
                        await self.dashboard.repository.save_analysis_event(event)
                        self.alerts.process_event(event, now)
                        logger.info(
                            "[%s] %s → %s %s",
                            symbol,
                            event.previous_state.value,
                            event.new_state.value,
                            event.direction.value if event.direction else "",
                        )
                # 更新 dashboard 各 symbol 状态
                self._refresh_dashboard(now)
                await asyncio.sleep(COMPUTE_INTERVAL_S)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("compute_loop_error")

    def _refresh_dashboard(self, now: int) -> None:
        """更新各 symbol 的最新 snapshot 供展示。"""
        for symbol in self.symbols:
            state = self.state_machine.get_symbol(symbol)
            self.dashboard.update_event(
                AnalysisEvent(
                    symbol=symbol,
                    direction=state.direction,
                    previous_state=state.state,
                    new_state=state.state,
                    evidence=[],
                    vetoes=[],
                    asof=now,
                    confidence_state=__import__("src.domain", fromlist=["ConfidenceState"]).ConfidenceState.CONFIDENT,
                )
            )

    def get_stats(self) -> dict:
        return {
            "symbols": self.symbols,
            "received": dict(self.received_count),
        }


pipeline: MarketDataPipeline | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    pipeline = MarketDataPipeline(SYMBOLS)
    await pipeline.start()
    logger.info("pipeline started, symbols=%s", SYMBOLS)
    yield
    await pipeline.stop()


app = FastAPI(
    title="资金行为驱动行情分析系统",
    description="实时 Market Radar",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def index():
    """Market Radar 页面。"""
    return HTMLResponse(INDEX_HTML)


@app.get("/api/radar")
async def get_radar():
    if pipeline is None:
        return []
    return pipeline.dashboard.get_market_radar()


@app.get("/api/stats")
async def get_stats():
    if pipeline is None:
        return {}
    return pipeline.get_stats()


@app.get("/api/symbol/{symbol}")
async def get_symbol_detail(symbol: str):
    if pipeline is None:
        return {"error": "not ready"}
    return pipeline.dashboard.get_symbol_detail(symbol) or {"symbol": symbol, "state": "NO_DATA"}


@app.get("/api/signals")
async def get_signals():
    if pipeline is None:
        return []
    return pipeline.dashboard.get_signal_history()


INDEX_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>资金行为雷达</title>
<style>
body { font-family: "Microsoft YaHei", "PingFang SC", monospace; background:#0d1117; color:#c9d1d9; padding:24px; }
h1 { color:#58a6ff; }
h2 { color:#8b949e; }
table { border-collapse:collapse; width:100%; }
th,td { border:1px solid #30363d; padding:8px 12px; text-align:left; }
th { background:#161b22; color:#8b949e; }
.badge { padding:2px 8px; border-radius:4px; font-weight:bold; font-size:12px; }
.state-sleeping { background:#484f58; }
.state-anomaly { background:#d29922; }
.state-suspected { background:#1f6feb; }
.state-confirmed { background:#238636; }
.state-continuation { background:#1f6feb; }
.state-exhaustion { background:#d29922; }
.state-withdrawal { background:#da3633; }
.state-rejected { background:#484f58; }
.state-cooldown { background:#484f58; }
.dir-long { background:#238636; }
.dir-short { background:#da3633; }
.conf-ok { color:#3fb950; }
.conf-warn { color:#d29922; }
.conf-unknown { color:#f85149; }
.symbol { color:#58a6ff; font-weight:bold; }
.card { background:#161b22; border:1px solid #30363d; border-radius:6px; padding:16px; margin:8px 0; }
#stats { margin-top:20px; color:#8b949e; }
</style>
<script>
function stateBadge(s) {
  const m = {SLEEPING:'沉睡',ANOMALY:'异常',SUSPECTED_START:'疑似启动',START_CONFIRMED:'启动确认',CONTINUATION:'持续',EXHAUSTION:'衰竭',WITHDRAWAL:'撤离',REJECTED:'已拒绝',COOLDOWN:'冷却'};
  return `<span class="badge state-${s.toLowerCase().replace('_','-')}">${m[s]||s}</span>`;
}
function dirBadge(d) {
  if (!d) return '-';
  return `<span class="badge dir-${d.toLowerCase()}">${d=='LONG'?'多头':'空头'}</span>`;
}
function confText(c) {
  return `<span class="conf-${c.toLowerCase()}">${c=='CONFIDENT'?'可信':c=='DEGRADED'?'降级':'未知'}</span>`;
}
async function refresh() {
  const radar = await fetch('/api/radar').then(r=>r.json());
  const stats = await fetch('/api/stats').then(r=>r.json());
  const signals = await fetch('/api/signals').then(r=>r.json());
  let html = '<table><tr><th>交易对</th><th>状态</th><th>方向</th><th>置信度</th><th>证据数</th><th>否决数</th></tr>';
  for (const r of radar) {
    html += `<tr><td class="symbol">${r.symbol}</td><td>${stateBadge(r.state)}</td><td>${dirBadge(r.direction)}</td><td>${confText(r.confidence_state)}</td><td>${r.evidence_count}</td><td>${r.veto_count}</td></tr>`;
  }
  html += '</table>';
  document.getElementById('radar').innerHTML = html;
  document.getElementById('stats').innerHTML = '已接收成交: ' + JSON.stringify(stats.received||{});
  let sigHtml = '<h2>信号历史</h2><table><tr><th>交易对</th><th>状态</th><th>方向</th><th>时间</th></tr>';
  for (const s of signals.slice(-10).reverse()) {
    sigHtml += `<tr><td>${s.symbol}</td><td>${stateBadge(s.state)}</td><td>${dirBadge(s.direction)}</td><td>${new Date(s.asof).toLocaleTimeString()}</td></tr>`;
  }
  sigHtml += '</table>';
  document.getElementById('signals').innerHTML = sigHtml;
}
setInterval(refresh, 2000);
refresh();
</script>
</head>
<body>
<h1>资金行为驱动行情雷达</h1>
<div class="card"><h2>市场雷达</h2><div id="radar">加载中...</div></div>
<div id="stats"></div>
<div class="card"><div id="signals"></div></div>
</body>
</html>
"""