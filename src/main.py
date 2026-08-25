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

from src.config import load_config
from src.runtime import MarketRadarRuntime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"

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


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML)


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


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>资金行为雷达</title>
<style>
body { font-family: "Microsoft YaHei","PingFang SC",monospace; background:#0d1117; color:#c9d1d9; padding:18px; margin:0; }
h1 { color:#58a6ff; margin:0 0 8px 0; }
.sub { color:#8b949e; font-size:13px; margin-bottom:14px; }
.summary { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
.chip { background:#161b22; border:1px solid #30363d; border-radius:6px; padding:6px 12px; font-size:13px; }
.chip b { color:#58a6ff; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:10px; }
.card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px; }
.card:hover { border-color:#58a6ff; }
.sym { color:#58a6ff; font-weight:bold; font-size:15px; }
.row { display:flex; justify-content:space-between; font-size:12px; margin:3px 0; color:#8b949e; }
.row span { color:#c9d1d9; }
.badge { padding:1px 7px; border-radius:4px; font-size:11px; font-weight:bold; }
.s-SLEEPING{background:#484f58} .s-ANOMALY{background:#d29922} .s-SUSPECTED_START{background:#1f6feb}
.s-START_CONFIRMED{background:#238636} .s-CONTINUATION{background:#1f6feb} .s-EXHAUSTION{background:#d29922}
.s-WITHDRAWAL{background:#da3633} .s-REJECTED{background:#484f58} .s-COOLDOWN{background:#484f58}
.d-LONG{background:#238636} .d-SHORT{background:#da3633}
.c-CONFIDENT{color:#3fb950} .c-DEGRADED{color:#d29922} .c-UNKNOWN{color:#f85149}
.pct-up{color:#3fb950} .pct-dn{color:#f85149}
a { color:#58a6ff; text-decoration:none; }
table { border-collapse:collapse; width:100%; font-size:12px; }
th,td { border:1px solid #30363d; padding:5px 8px; text-align:left; }
th { background:#161b22; color:#8b949e; }
.h-LIVE{color:#3fb950} .h-DEGRADED{color:#d29922} .h-STALE{color:#f85149} .h-FAIL{color:#f85149} .h-OK{color:#3fb950} .h-WARN{color:#d29922}
#detail { margin-top:14px; }
</style>
<script>
const S={SLEEPING:'沉睡',ANOMALY:'异常',SUSPECTED_START:'疑似启动',START_CONFIRMED:'启动确认',CONTINUATION:'延续',EXHAUSTION:'衰竭',WITHDRAWAL:'撤离',REJECTED:'已拒绝',COOLDOWN:'冷却'};
function badge(s){return `<span class="badge s-${s}">${S[s]||s}</span>`;}
function dirb(d){if(!d)return '-';return `<span class="badge d-${d}">${d=='LONG'?'多':'空'}</span>`;}
function conf(c){return `<span class="c-${c}">${c=='CONFIDENT'?'可信':c=='DEGRADED'?'降级':'未知'}</span>`;}
function pct(p){if(p==null)return '-';const c=p>=0?'pct-up':'pct-dn';return `<span class="${c}">${(p>=0?'+':'')}${p.toFixed(2)}%</span>`;}
async function refresh(){
  const [radar,stats,signals] = await Promise.all([
    fetch('/api/radar').then(r=>r.json()),
    fetch('/api/stats').then(r=>r.json()),
    fetch('/api/signals').then(r=>r.json()),
  ]);
  let sc=stats.state_counts||{};
  document.getElementById('summary').innerHTML=
    `<div class="chip">数据：<b>${stats.circuit_open||false}</b></div>`+
    `<div class="chip">Universe：<b>${stats.universe_size??0}</b></div>`+
    `<div class="chip">深度：<b>${stats.deep_size??0}</b></div>`+
    `<div class="chip">候选：<b>${stats.candidate_count??0}</b></div>`+
    `<div class="chip">异常：<b>${sc.ANOMALY||0}</b></div>`+
    `<div class="chip">疑似启动：<b>${sc.SUSPECTED_START||0}</b></div>`+
    `<div class="chip">确认启动：<b>${sc.START_CONFIRMED||0}</b></div>`+
    `<div class="chip">延续：<b>${sc.CONTINUATION||0}</b></div>`+
    `<div class="chip">衰竭：<b>${sc.EXHAUSTION||0}</b></div>`+
    `<div class="chip">撤离：<b>${sc.WITHDRAWAL||0}</b></div>`;
  let h='';
  if(!radar.length){h='<div class="sub">暂无数据，等待采集...</div>';}
  for(const r of radar){
    h+=`<div class="card" onclick="loadDetail('${r.symbol}')">`+
      `<div><span class="sym">${r.symbol}</span> ${badge(r.state)} ${dirb(r.direction)} ${conf(r.confidence_state)}</div>`+
      `<div class="row">24h涨跌 <span>${pct(r.price_change_24h)}</span></div>`+
      `<div class="row">24h成交额 <span>${fmtVol(r.quote_volume_24h)}</span></div>`+
      `<div class="row">证据/否决 <span>${r.evidence_count} / ${r.veto_count}</span></div>`+
      `<div class="row">更新 <span>${ago(r.last_update_ms)}</span></div>`+
      `</div>`;
  }
  document.getElementById('radar').innerHTML=h;
  let sh='<h2 class="sub">信号历史</h2><table><tr><th>交易对</th><th>状态</th><th>方向</th><th>时间</th><th>证据</th></tr>';
  for(const s of signals.slice(-15).reverse()){sh+=`<tr><td>${s.symbol}</td><td>${badge(s.state)}</td><td>${dirb(s.direction)}</td><td>${ts(s.asof)}</td><td>${s.evidence_count}</td></tr>`;}
  sh+='</table>';
  document.getElementById('signals').innerHTML=sh;
}
async function loadDetail(sym){
  const d=await fetch('/api/symbol/'+sym).then(r=>r.json());
  if(d.error){document.getElementById('detail').innerHTML=d.error;return;}
  let h=`<div class="card"><h2 class="sym">${sym} 详情</h2>`;
  h+=`<div class="row">状态 <span>${badge(d.state)} ${dirb(d.direction)} ${conf(d.confidence_state)}</span></div>`;
  h+=`<h3 class="sub">证据链（${d.evidence.length}）</h3>`;
  for(const e of d.evidence){h+=`<div class="row">[${e.family}] ${e.type} <span>${fmt(e.value)} ${e.passed?'✓':'✗'} (${e.threshold!=null?'阈值'+e.threshold:''})</span></div>`;}
  h+=`<h3 class="sub">否决（${d.vetoes.length}）</h3>`;
  for(const v of d.vetoes){h+=`<div class="row">${v.type} <span class="${v.triggered?'pct-dn':'pct-up'}">${v.triggered?'命中':'未命中'} (${v.severity})</span></div>`;}
  h+='</div>';
  document.getElementById('detail').innerHTML=h;
}
function fmt(v){return v==null?'-':(typeof v=='number'?v.toFixed(4):v);}
function fmtVol(v){if(!v)return '-';if(v>1e9)return (v/1e9).toFixed(2)+'B';if(v>1e6)return (v/1e6).toFixed(1)+'M';if(v>1e3)return (v/1e3).toFixed(1)+'K';return v.toFixed(0);}
function ago(ms){if(!ms)return '-';const s=(Date.now()-ms)/1000;return s<60?s.toFixed(0)+'s前':(s/60).toFixed(0)+'m前';}
function ts(ms){return new Date(ms).toLocaleTimeString();}
setInterval(refresh,2000);refresh();
</script>
</head>
<body>
<h1>资金行为雷达</h1>
<div class="sub">资金异动 → 疑似启动 → 假启动过滤 → 确认启动 → 延续 → 衰竭 → 撤离</div>
<div id="summary" class="summary"></div>
<div id="radar" class="grid"></div>
<div id="detail"></div>
<div id="signals"></div>
</body>
</html>
"""
