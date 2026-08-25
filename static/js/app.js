/**
 * 资金行为雷达 V1.3 — 主应用（P3 UI 重构）
 * SPA 路由 + 状态管理 + 页面渲染
 * 页面：首页 / 全市场 / 监督台 / 模拟验证 / 数据健康
 * 依据：V1.3 更新计划 §12-§17 / §33-§42 / §51-§57 / §63-§71
 */

// ═══════════════════════════════════════
// 全局状态
// ═══════════════════════════════════════
const State = {
  currentPage: 'home',
  selectedSymbol: null,
  searchQuery: '',
  filter: 'all',
  sortKey: 'ranking',
  sortDir: 'desc',
  // V1.3 P3 API 数据
  homeData: null,          // §64 GET /api/home
  marketData: null,        // GET /api/market（结论 + 市场背景 + 统计 + top10）
  radarData: [],
  statsData: {},
  healthData: [],
  supervisionData: {},     // §65 GET /api/supervision（按池分组）
  simData: null,           // GET /api/simulations {queue, positions, open_positions, results}
  statisticsData: null,    // GET /api/statistics {overview, buckets, setup_conversion}
  pricesData: {},
  detailCache: {},
  detailSymbol: null,
  loading: true,
  previewSymbol: null,
  simTab: 'waiting',       // §33: waiting | running | closed | stats | replay
  // §54 分数趋势：symbol → { opp, sc, dc, subs }（上一轮 slow-poll 30s 快照）
  scoreTrend: {},
  // §12/§66.8 首页不秒级重排：材料变化节流
  lastRendered: {},
};

// ═══════════════════════════════════════
// 常量映射
// ═══════════════════════════════════════
// §34/§35 模拟状态中文
const SIM_STATUS_LABELS = {
  WATCHING: '等待回踩', ENTRY_ZONE_REACHED: '进入关注区', REVALIDATING: '二次验证中',
  ARMED: '等待入场', SIMULATED_ENTRY: '已模拟入场', OPEN: '模拟跟踪中',
  CLOSED: '已结束', EXPIRED: '已过期', CANCELLED: '已取消',
  INVALIDATED: '已失效', MISSED: '已错过',
};
// §36 退出原因中文
const EXIT_REASON_LABELS = {
  TP1_HIT: 'TP1 止盈', TP2_HIT: 'TP2 止盈', TP3_HIT: 'TP3 止盈',
  INVALIDATION_HIT: '结构失效', SIGNAL_WITHDRAWAL: '资金撤离',
  DISTRIBUTION_EXIT: '派发退出', DIRECTION_FLIP: '方向翻转',
  TIME_EXPIRED: '时间耗尽', MANUAL_CLOSE: '手动平仓',
};
// §40 监督台 Kanban 列
const KANBAN_COLUMNS = [
  ['anomaly', '异动观察'],
  ['watch', '等待确认'],
  ['confirmed', '确认机会'],
  ['continuation', '趋势跟踪'],
  ['risk', '风险'],
  ['exit', '撤离'],
];
// §35 运行中非持久化字段 → —（不显示实时资金摘要）
const RUNNING_NA_FIELDS = ['资金变化', '撤离风险'];
// §53 监督阶段（首页卡片/监督台阶段徽标）
function supervisionStage(row) {
  const pool = (row.supervision && row.supervision.current_pool) || row.current_pool;
  const sim = row.simulation_status;
  if (sim === 'SIMULATED_ENTRY' || sim === 'OPEN') return '模拟跟踪中';
  if (sim === 'REVALIDATING') return '等待二次确认';
  if (sim === 'ARMED') return '确认启动';
  if (sim === 'WATCHING' || sim === 'ENTRY_ZONE_REACHED') return '等待回踩';
  if (pool === 'continuation') return '趋势跟踪';
  if (pool === 'risk') return '资金衰减';
  if (pool === 'exit') return '撤离观察';
  if (pool === 'confirmed') return '确认启动';
  return '—';
}
// §54 方向箭头（30s 稳定窗口；首轮 / 无变化 → →）
function trendArrow(cur, prev) {
  if (prev == null || cur == null) return '→';
  const d = cur - prev;
  if (d > 1) return '↑';
  if (d < -1) return '↓';
  return '→';
}
function arrowClass(arrow) {
  if (arrow === '↑') return 'text-long';
  if (arrow === '↓') return 'text-short';
  return 'text-muted';
}

// ═══════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════
function fmt(v, d = 2) {
  if (v == null || isNaN(v)) return '-';
  return Number(v).toFixed(d);
}
function fmtVol(v) {
  if (!v) return '-';
  if (v > 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (v > 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (v > 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(0);
}
function fmtPct(p) {
  if (p == null || isNaN(p)) return '-';
  const sign = p >= 0 ? '+' : '';
  return sign + p.toFixed(2) + '%';
}
function pctColor(p) {
  if (p == null) return '';
  return p >= 0 ? 'text-long' : 'text-short';
}
function ago(ms) {
  if (!ms) return '-';
  const s = (Date.now() - ms) / 1000;
  if (s < 5) return '刚刚';
  if (s < 60) return Math.floor(s) + 's前';
  if (s < 3600) return Math.floor(s / 60) + 'm前';
  if (s < 86400) return Math.floor(s / 3600) + 'h前';
  return Math.floor(s / 86400) + 'd前';
}
function ts(ms) {
  if (!ms) return '-';
  return new Date(ms).toLocaleTimeString('zh-CN', { hour12: false });
}
function tsFull(ms) {
  if (!ms) return '-';
  return new Date(ms).toLocaleString('zh-CN', { hour12: false });
}
function fmtDurHours(h) {
  if (h == null || isNaN(h)) return '-';
  if (h < 1) return Math.round(h * 60) + 'm';
  return h.toFixed(1) + 'h';
}
function fmtPrice(p) {
  if (p == null || isNaN(p)) return '-';
  if (p >= 1000) return Number(p).toFixed(2);
  if (p >= 1) return Number(p).toFixed(4);
  if (p >= 0.01) return Number(p).toFixed(5);
  return Number(p).toPrecision(4);
}
function scoreColor(score, isRisk) {
  if (score == null) return 'low';
  if (isRisk) return score > 50 ? 'risk-high' : 'risk-low';
  if (score >= 70) return 'high';
  if (score >= 40) return 'mid';
  return 'low';
}
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function dash(v) {
  return v == null || v === '' ? '—' : v;
}

// ═══════════════════════════════════════
// 路由
// ═══════════════════════════════════════
function navigate(hash) {
  window.location.hash = hash;
}
function parseRoute() {
  const hash = window.location.hash.slice(1) || '/';
  const parts = hash.split('/').filter(Boolean);
  if (parts.length === 0) return { page: 'home' };
  if (parts[0] === 'market') return { page: 'market' };
  if (parts[0] === 'supervision') return { page: 'supervision' };
  if (parts[0] === 'simulations') return { page: 'simulations' };
  if (parts[0] === 'health') return { page: 'health' };
  return { page: 'home' };
}
function handleRoute() {
  const route = parseRoute();
  State.currentPage = route.page;
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === route.page);
  });
  renderPage();
}

// ═══════════════════════════════════════
// 页面渲染入口
// ═══════════════════════════════════════
function renderPage() {
  const view = document.getElementById('view');
  switch (State.currentPage) {
    case 'home': renderHome(view); break;
    case 'market': renderMarket(view); break;
    case 'supervision': renderSupervision(view); break;
    case 'simulations': renderSimulations(view); break;
    case 'health': renderHealth(view); break;
  }
}

// ═══════════════════════════════════════
// 首页（§12-§16 / §52-§55 / §69）
// ═══════════════════════════════════════
function renderHome(view) {
  const home = State.homeData;
  const stats = State.statsData;

  if (State.loading && !home) {
    view.innerHTML = `
      <div class="loading-text">
        <div>系统预热中</div>
        <div class="warmup-bar"><div class="fill" style="width:40%"></div></div>
        <div class="text-muted" style="font-size:0.75rem">正在建立数据基线<span class="dots"></span></div>
      </div>`;
    return;
  }

  const confirmed = home && home.confirmed_opportunities ? home.confirmed_opportunities : [];
  const watch = home && home.watch_candidates ? home.watch_candidates : [];
  const risk = home && home.risk_candidates ? home.risk_candidates : [];
  const regime = home && home.market_regime;
  const health = home && home.health;
  const universe = stats.universe_size || (health && health.total_pairs) || 0;

  let html = '';

  // §15 首页顶部字段：市场背景 / 数据健康 / Universe / 重点观察数 / 确认机会数 / 风险中数量
  html += `<div class="top-stats">
    <div class="top-stat"><span class="label">市场背景</span>
      <span class="value">${regime ? escapeHtml(regime.label || '—') : '—'}</span>
      ${regime && regime.detail ? `<span class="sub">${escapeHtml(regime.detail)}</span>` : ''}
    </div>
    <div class="top-stat"><span class="label">数据健康</span>
      <span class="value">${health ? escapeHtml(health.level_label || '—') : '—'}</span>
      ${health && health.coverage_pct != null ? `<span class="sub">${fmt(health.coverage_pct, 0)}% 覆盖</span>` : ''}
    </div>
    <div class="top-stat"><span class="label">Universe</span><span class="value accent">${universe || '—'}</span></div>
    <div class="top-stat"><span class="label">重点观察</span><span class="value warning">${watch.length}</span></div>
    <div class="top-stat"><span class="label">确认机会</span><span class="value long">${confirmed.length}</span></div>
    <div class="top-stat"><span class="label">风险中</span><span class="value short">${risk.length}</span></div>
  </div>`;

  // §13 Top Opportunities：最多 10 个，不强制凑满；0 个显示空态文案
  html += `<div class="section-title">Top Opportunities <span class="count">${confirmed.length}/10</span></div>`;

  if (confirmed.length === 0) {
    html += `<div class="empty-state compact">
      <div class="icon">📡</div>
      <div class="title">当前暂无确认机会。</div>
      <div class="desc">系统正在重点观察 ${watch.length} 个候选。</div>
    </div>`;
  } else {
    html += '<div class="card-grid">';
    for (let i = 0; i < confirmed.length; i++) {
      html += renderHomeCard(confirmed[i], i + 1);
    }
    html += '</div>';
  }

  // §14 正在观察（最多 5 个；不给正式 Trade Plan）
  if (watch.length > 0) {
    html += `<div class="section-title">正在观察 <span class="count">${watch.length}</span></div>`;
    html += '<div class="watch-grid">';
    for (const w of watch.slice(0, 5)) {
      const dir = w.direction_label ? `<span class="badge badge-${(w.direction || '').toLowerCase()}">${escapeHtml(w.direction_label)}</span>` : '';
      html += `<div class="watch-card" onclick="selectSymbol('${w.symbol}')">
        <div class="watch-head">
          <span class="card-symbol">${w.symbol}</span>
          <span>
            <span class="badge badge-state-${w.state}">${escapeHtml(w.state_label || w.state)}</span>
            ${dir}
          </span>
        </div>
        <div class="watch-body">
          <div class="watch-row"><span class="label">机会</span><span class="value text-accent">${w.opportunity_score != null ? fmt(w.opportunity_score, 1) : '—'}</span></div>
          <div class="watch-row"><span class="label">确认</span><span class="value">${w.signal_confirmation != null ? fmt(w.signal_confirmation, 0) + '%' : '—'}</span></div>
        </div>
        <div class="card-summary">${escapeHtml(w.summary || '还缺：确认条件')}</div>
      </div>`;
    }
    html += '</div>';
  }

  // 风险中（小条幅）
  if (risk.length > 0) {
    html += `<div class="section-title">风险中 <span class="count short">${risk.length}</span></div>`;
    html += '<div class="risk-strip">';
    for (const r of risk.slice(0, 5)) {
      html += `<div class="risk-chip" onclick="selectSymbol('${r.symbol}')">
        <span class="sym">${r.symbol}</span>
        <span class="text-muted">${escapeHtml(r.state_label || r.state)}</span>
        ${r.pump_risk != null ? `<span class="text-short">Pump ${fmt(r.pump_risk, 0)}</span>` : ''}
        ${r.distribution_risk != null ? `<span class="text-short">派发 ${fmt(r.distribution_risk, 0)}</span>` : ''}
      </div>`;
    }
    html += '</div>';
  }

  view.innerHTML = html;

  // §12/§66.8 快照本次渲染，供下次节流比较
  State.lastRendered.home = {
    confirmed: confirmed.map(s => ({
      symbol: s.symbol, state: s.state, direction: s.direction,
      opp: snapValue(s, 'opportunity_score'),
      sc: snapValue(s, 'signal_confirmation'),
      subs: s.live_subscores || {},
    })),
    watchSymbols: watch.map(w => w.symbol).join(','),
    riskSymbols: risk.map(r => r.symbol).join(','),
  };
}

// §55 首页主值 = 推荐时快照（冻结），实时变化放 Drawer（§56）
function snapValue(row, key) {
  const dec = (row.decision_snapshot && row.decision_snapshot.decision) || {};
  const v = dec[key];
  return v != null ? v : row[key];
}
function liveValue(row, key) {
  return row[key];
}

// §16 首页正式机会卡片
function renderHomeCard(s, rank) {
  const dir = s.direction || '';
  const dirClass = dir === 'LONG' ? 'long' : (dir === 'SHORT' ? 'short' : '');
  const plan = (s.decision_snapshot && s.decision_snapshot.decision && s.decision_snapshot.decision.trade_plan) || s.trade_plan;
  const price = State.pricesData[s.symbol] || s.current_price;
  const trend = State.scoreTrend[s.symbol];

  // 主值=快照（§55）
  const opp = snapValue(s, 'opportunity_score');
  const sc = snapValue(s, 'signal_confirmation');
  const dc = snapValue(s, 'data_confidence');
  // §54 趋势箭头（30s 稳定窗口，实时值方向）
  const liveOpp = liveValue(s, 'opportunity_score');
  const aOpp = trendArrow(liveOpp, trend && trend.opp);
  const aSc = trendArrow(liveValue(s, 'signal_confirmation'), trend && trend.sc);

  // §16 子评分条：资金输入/启动质量/持续启动/即时续航/吸筹迹象/追涨安全/撤离风险
  const subs = s.live_subscores || {};
  const prevSubs = (trend && trend.subs) || {};
  let subTiles = [
    ['capital_inflow', '资金输入'],
    ['startup_quality', '启动质量'],
    ['sustained_startup', '持续启动'],
    ['immediate_stamina', '即时续航'],
  ].map(([k, label]) => {
    const v = subs[k];
    const a = trendArrow(v, prevSubs[k]);
    return `<div class="sub-tile"><span class="label">${label}</span><span class="val ${scoreColor(v, false)}">${v != null ? fmt(v, 0) : '—'}</span><span class="arrow ${arrowClass(a)}">${a}</span></div>`;
  }).join('');
  const accumA = trendArrow(s.accumulation_score, trend && trend.accum);
  const chaseA = trendArrow(subs.chase_safety, prevSubs.chase_safety);
  const wdA = trendArrow(subs.withdrawal_risk, prevSubs.withdrawal_risk);
  subTiles += `
    <div class="sub-tile"><span class="label">吸筹迹象</span><span class="val ${scoreColor(s.accumulation_score, true)}">${s.accumulation_score != null ? fmt(s.accumulation_score, 0) : '—'}</span><span class="arrow ${arrowClass(accumA)}">${accumA}</span></div>
    <div class="sub-tile"><span class="label">追涨安全</span><span class="val ${scoreColor(subs.chase_safety, false)}">${subs.chase_safety != null ? fmt(subs.chase_safety, 0) : '—'}</span><span class="arrow ${arrowClass(chaseA)}">${chaseA}</span></div>
    <div class="sub-tile risk"><span class="label">撤离风险</span><span class="val ${scoreColor(subs.withdrawal_risk, true)}">${subs.withdrawal_risk != null ? fmt(subs.withdrawal_risk, 0) : '—'}</span><span class="arrow ${arrowClass(wdA)}">${wdA}</span></div>`;

  // §53 监督阶段 / §52 模拟小状态
  const stage = supervisionStage(s);
  const simBadge = s.simulation_status
    ? `<span class="sim-mini ${s.simulation_status === 'OPEN' || s.simulation_status === 'SIMULATED_ENTRY' ? 'live' : ''}">${SIM_STATUS_LABELS[s.simulation_status] || s.simulation_status}</span>` : '';

  // §16 当前计划摘要（仅正式状态有正式计划）
  let planSummary = '—';
  if (plan && plan.status === 'ACTIVE') {
    if (plan.plan_reason) planSummary = plan.plan_reason;
    else if (plan.reference_entry_low != null && plan.reference_entry_high != null) {
      planSummary = `等待 ${fmtPrice(plan.reference_entry_low)} ~ ${fmtPrice(plan.reference_entry_high)} 回踩重新确认`;
    }
  } else if (s.state === 'SUSPECTED_START') {
    planSummary = '候选预案，尚未确认';
  }

  const summary = (s.decision_snapshot && s.decision_snapshot.decision && s.decision_snapshot.decision.summary) || s.summary || '';

  return `
    <div class="card ${dirClass}" onclick="selectSymbol('${s.symbol}')">
      <div class="card-header">
        <div style="display:flex;align-items:center;gap:8px">
          <span class="card-rank">#${rank}</span>
          <span class="card-symbol">${s.symbol}</span>
          ${simBadge}
        </div>
        <div style="display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end">
          <span class="badge badge-state-${s.state}">${escapeHtml(s.state_label || s.state)}</span>
          ${dir ? `<span class="badge badge-${dir.toLowerCase()}">${escapeHtml(s.direction_label || dir)}</span>` : ''}
          ${s.setup_label ? `<span class="badge badge-setup">${escapeHtml(s.setup_label)}</span>` : ''}
        </div>
      </div>
      <div class="card-price">
        <span class="font-mono" data-price-symbol="${s.symbol}">${fmtPrice(price)}</span>
        ${fmtPct(s.price_change_24h)} <span class="${pctColor(s.price_change_24h)}" style="font-size:0.7rem">24h</span>
      </div>
      <div class="card-meta">
        <span class="text-muted">主周期</span> <span class="font-mono">${dash(s.primary_timeframe)}</span>
        <span class="text-muted" style="margin-left:12px">监督阶段</span> <span class="stage-badge">${escapeHtml(stage)}</span>
      </div>
      <div class="card-scores">
        <div class="score-row"><span class="label">机会分</span><span class="value big text-accent">${opp != null ? fmt(opp, 1) : '—'}</span><span class="arrow ${arrowClass(aOpp)}">${aOpp}</span></div>
        <div class="score-row"><span class="label">信号确认</span><span class="value">${sc != null ? fmt(sc, 0) + '%' : '—'}</span><span class="arrow ${arrowClass(aSc)}">${aSc}</span></div>
        <div class="score-row"><span class="label">数据可信</span><span class="value">${dc != null ? fmt(dc, 0) + '%' : '—'}</span></div>
      </div>
      <div class="sub-tiles">${subTiles}</div>
      ${summary ? `<div class="card-summary">${escapeHtml(summary)}</div>` : ''}
      <div class="card-plan">${escapeHtml(planSummary)}</div>
    </div>`;
}

// ═══════════════════════════════════════
// 全市场（表格保留，数据源 getMarket + getRadar）
// ═══════════════════════════════════════
function renderMarket(view) {
  let radar = State.radarData || [];

  if (State.searchQuery) {
    const q = State.searchQuery.toUpperCase();
    radar = radar.filter(s => s.symbol.includes(q));
  }
  if (State.filter !== 'all') {
    radar = radar.filter(s => s.state === State.filter);
  }

  const sortKey = State.sortKey;
  const dir = State.sortDir === 'asc' ? 1 : -1;
  radar.sort((a, b) => {
    let va, vb;
    switch (sortKey) {
      case 'opportunity': va = a.opportunity_score || 0; vb = b.opportunity_score || 0; break;
      case 'confidence': va = a.data_confidence || 0; vb = b.data_confidence || 0; break;
      case 'capital': va = a.score_breakdown && a.score_breakdown.subscores && a.score_breakdown.subscores.capital_inflow ? a.score_breakdown.subscores.capital_inflow.score : 0; vb = b.score_breakdown && b.score_breakdown.subscores && b.score_breakdown.subscores.capital_inflow ? b.score_breakdown.subscores.capital_inflow.score : 0; break;
      case 'price_change': va = a.price_change_24h || 0; vb = b.price_change_24h || 0; break;
      case 'updated': va = a.last_update_ms || 0; vb = b.last_update_ms || 0; break;
      default: va = (a.opportunity_score || 0) * (a.data_confidence || 0); vb = (b.opportunity_score || 0) * (b.data_confidence || 0);
    }
    return (va - vb) * dir;
  });

  const filters = [
    { key: 'all', label: '全部' },
    { key: 'START_CONFIRMED', label: '确认启动' },
    { key: 'CONTINUATION', label: '趋势延续' },
    { key: 'SUSPECTED_START', label: '疑似启动' },
    { key: 'ANOMALY', label: '异动观察' },
    { key: 'EXHAUSTION', label: '衰竭' },
    { key: 'WITHDRAWAL', label: '撤离' },
  ];
  const sortOptions = [
    { key: 'ranking', label: '排名' },
    { key: 'opportunity', label: '机会分' },
    { key: 'confidence', label: '数据可信' },
    { key: 'capital', label: '资金输入' },
    { key: 'price_change', label: '24h涨跌' },
    { key: 'updated', label: '更新时间' },
  ];

  let filterHtml = '<div class="filter-bar"><span class="filter-label">筛选</span>';
  for (const f of filters) {
    filterHtml += `<div class="filter-btn ${State.filter === f.key ? 'active' : ''}" onclick="setFilter('${f.key}')">${f.label}</div>`;
  }
  filterHtml += '</div>';
  filterHtml += '<div class="filter-bar"><span class="filter-label">排序</span>';
  for (const s of sortOptions) {
    filterHtml += `<div class="filter-btn ${State.sortKey === s.key ? 'active' : ''}" onclick="setSort('${s.key}')">${s.label}</div>`;
  }
  filterHtml += `<div class="filter-btn" onclick="toggleSortDir()">${State.sortDir === 'asc' ? '↑' : '↓'}</div></div>`;

  if (radar.length === 0) {
    view.innerHTML = filterHtml + `
      <div class="empty-state">
        <div class="icon">🔍</div>
        <div class="title">无匹配结果</div>
        <div class="desc">没有找到符合条件的交易对。试试调整筛选或搜索条件。</div>
      </div>`;
    return;
  }

  let tableHtml = `
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th style="width:40px">#</th>
          <th onclick="setSort('ranking')">币种</th>
          <th onclick="setSort('opportunity')" class="${State.sortKey === 'opportunity' ? 'sorted' : ''} ${State.sortDir}">机会</th>
          <th onclick="setSort('confidence')" class="${State.sortKey === 'confidence' ? 'sorted' : ''} ${State.sortDir}">可信</th>
          <th>状态</th>
          <th>方向</th>
          <th>Setup</th>
          <th onclick="setSort('capital')">资金输入</th>
          <th onclick="setSort('price_change')">24h</th>
          <th>更新</th>
        </tr></thead><tbody>`;

  for (let i = 0; i < radar.length; i++) {
    const s = radar[i];
    const dir = s.direction || '';
    const opp = s.opportunity_score;
    const dc = s.data_confidence_pct;
    const cap = s.score_breakdown && s.score_breakdown.subscores && s.score_breakdown.subscores.capital_inflow
      ? s.score_breakdown.subscores.capital_inflow.score : null;

    tableHtml += `
      <tr class="clickable" onclick="selectSymbol('${s.symbol}')">
        <td class="text-muted">${i + 1}</td>
        <td><span style="font-weight:600;color:var(--accent)">${s.symbol}</span></td>
        <td class="num">${opp != null ? fmt(opp, 1) : '-'}</td>
        <td class="num">${dc != null ? fmt(dc, 0) + '%' : '-'}</td>
        <td><span class="badge badge-state-${s.state}">${escapeHtml(s.state_label || s.state)}</span></td>
        <td>${dir ? `<span class="badge badge-${dir.toLowerCase()}">${escapeHtml(s.direction_label || dir)}</span>` : '-'}</td>
        <td>${escapeHtml(s.setup_label || '-')}</td>
        <td class="num">${cap != null ? fmt(cap, 0) : '-'}</td>
        <td class="num ${pctColor(s.price_change_24h)}">${fmtPct(s.price_change_24h)}</td>
        <td class="text-muted" style="font-size:0.7rem">${ago(s.last_update_ms)}</td>
      </tr>`;
  }

  tableHtml += '</tbody></table></div>';
  view.innerHTML = filterHtml + tableHtml;
}

function setFilter(key) { State.filter = key; renderPage(); }
function setSort(key) {
  if (State.sortKey === key) { toggleSortDir(); return; }
  State.sortKey = key;
  State.sortDir = 'desc';
  renderPage();
}
function toggleSortDir() { State.sortDir = State.sortDir === 'asc' ? 'desc' : 'asc'; renderPage(); }

// ═══════════════════════════════════════
// 监督台（§40 / §41 / §42 / §65）
// ═══════════════════════════════════════
function renderSupervision(view) {
  const kanban = State.supervisionData || {};

  let html = `<div class="supervision-head">
    <div class="page-title">监督台</div>
    <div class="text-muted sub">每个池独立监督规则 · 状态迁移有滞回 · 生命周期可追踪</div>
  </div>`;

  html += '<div class="kanban">';
  for (const [pool, label] of KANBAN_COLUMNS) {
    const rows = kanban[pool] || [];
    html += `<div class="kanban-col" data-pool="${pool}">
      <div class="kanban-head"><span class="dot"></span>${label}<span class="count">${rows.length}</span></div>
      <div class="kanban-body">`;
    for (const r of rows) {
      html += renderSupervisionCard(r);
    }
    if (rows.length === 0) {
      html += `<div class="kanban-empty">—</div>`;
    }
    html += '</div></div>';
  }
  html += '</div>';

  // §42 状态日志（点击卡片后填充到右侧 Drawer；这里提示）
  html += `<div class="text-muted" style="font-size:0.75rem;margin-top:10px">点击任意卡片查看该 symbol 的监督详情与 Setup 时间线（§42 一个 Setup 一条 Timeline）。</div>`;

  view.innerHTML = html;
}

// §41 监督台卡片字段
function renderSupervisionCard(r) {
  const dir = r.direction || '';
  const dirHtml = dir ? `<span class="badge badge-${dir.toLowerCase()}">${escapeHtml(r.direction_label || dir)}</span>` : '';
  return `
    <div class="sup-card" onclick="openSupervisionSymbol('${r.symbol}')">
      <div class="sup-head"><span class="sym">${r.symbol}</span>
        <span><span class="badge badge-state-${r.current_state}">${escapeHtml(r.state_label || r.current_state)}</span>${dirHtml}</span>
      </div>
      <div class="sup-meta">
        ${r.setup_label ? `<span class="chip-mini">${escapeHtml(r.setup_label)}</span>` : ''}
        <span class="text-muted">已观察 ${ago(r.entered_state_at)}</span>
      </div>
      <div class="sup-scores">
        <div class="score-row"><span class="label">机会</span><span class="value">${r.opportunity_score != null ? fmt(r.opportunity_score, 1) : '—'}</span></div>
        <div class="score-row"><span class="label">确认</span><span class="value">${r.signal_confirmation != null ? fmt(r.signal_confirmation, 0) + '%' : '—'}</span></div>
      </div>
      <div class="sup-question">${escapeHtml(r.supervision_question || '')}</div>
    </div>`;
}

async function openSupervisionSymbol(symbol) {
  const drawer = document.getElementById('side-drawer');
  const overlay = document.getElementById('drawer-overlay');
  if (!drawer) return;
  drawer.classList.add('open');
  if (overlay) overlay.classList.add('show');
  drawer.innerHTML = '<div class="drawer-loading">加载监督详情<span class="dots"></span></div>';
  const detail = await API.getSupervisionSymbol(symbol);
  if (!detail) {
    drawer.innerHTML = '<div class="drawer-loading">数据不足</div>';
    return;
  }
  drawer.innerHTML = renderSupervisionDrawer(detail);
}

// 监督详情 Drawer：§41 字段 + §42 Setup 时间线（状态日志）
function renderSupervisionDrawer(d) {
  const dir = d.direction || '';
  let html = `<div class="drawer-section">
    <div class="drawer-header">
      <span class="drawer-symbol">${d.symbol}</span>
      <button class="drawer-close" onclick="closeDrawer()">✕</button>
    </div>
    <div class="drawer-badges">
      <span class="badge badge-state-${d.current_state}">${escapeHtml(d.state_label || d.current_state)}</span>
      ${dir ? `<span class="badge badge-${dir.toLowerCase()}">${escapeHtml(d.direction_label || dir)}</span>` : ''}
      ${d.setup_label ? `<span class="badge badge-setup">${escapeHtml(d.setup_label)}</span>` : ''}
    </div>
  </div>`;

  html += `<div class="drawer-section">
    <div class="drawer-title">监督状态</div>
    <div class="drawer-tp">
      <div><span class="text-muted">监督池</span> <span class="value">${escapeHtml(d.current_pool || '—')}</span></div>
      <div><span class="text-muted">监督级别</span> <span class="value">${escapeHtml(d.supervision_level || '—')}</span></div>
      <div><span class="text-muted">进入本池</span> <span class="value">${tsFull(d.entered_pool_at)}</span></div>
      <div><span class="text-muted">进入本状态</span> <span class="value">${tsFull(d.entered_state_at)}</span></div>
      <div><span class="text-muted">连续失败</span> <span class="value">${dash(d.condition_fail_streak)}</span></div>
      <div><span class="text-muted">上次动作</span> <span class="value">${escapeHtml(d.last_action || '—')}</span></div>
      <div><span class="text-muted">当前价格</span> <span class="value font-mono">${fmtPrice(d.current_price)}</span></div>
      <div><span class="text-muted">派发风险</span> <span class="value">${d.distribution_risk != null ? fmt(d.distribution_risk, 0) : '—'}</span></div>
      <div><span class="text-muted">Pump风险</span> <span class="value">${d.pump_risk != null ? fmt(d.pump_risk, 0) : '—'}</span></div>
    </div>
    <div class="sup-question big">${escapeHtml(d.supervision_question || '')}</div>
  </div>`;

  // §42 一个 Setup 一条 Timeline（状态日志）
  if (d.timeline && d.timeline.length > 0) {
    html += `<div class="drawer-section"><div class="drawer-title">状态日志 · Setup 时间线</div><div class="timeline">`;
    const tl = [...d.timeline].reverse();
    for (const t of tl) {
      html += `<div class="timeline-item"><span class="time">${ts(t.time)}</span><span class="label">${escapeHtml(t.state || t.state_label)}</span></div>`;
    }
    html += `</div></div>`;
  }

  return html;
}

// ═══════════════════════════════════════
// 模拟验证（§33-§39 / §71）
// ═══════════════════════════════════════
const SIM_ACTIVE_WAITING = ['WATCHING', 'ENTRY_ZONE_REACHED', 'REVALIDATING', 'ARMED', 'SIMULATED_ENTRY'];
const SIM_TERMINAL = ['CLOSED', 'EXPIRED', 'CANCELLED', 'INVALIDATED', 'MISSED'];

function renderSimulations(view) {
  const data = State.simData || { queue: [], positions: [], open_positions: [], results: [] };
  const queue = data.queue || [];
  const positions = data.positions || [];
  const openPos = data.open_positions || [];
  const results = data.results || [];

  const waiting = queue.filter(i => SIM_ACTIVE_WAITING.includes(i.status));
  const running = openPos;
  const closedQueue = queue.filter(i => SIM_TERMINAL.includes(i.status));
  // 已结束 = 终态队列项 + 有结果的（合并结果字段）
  const resultById = {};
  for (const r of results) resultById[r.simulation_id] = r;
  const closed = closedQueue.map(i => ({ ...i, result: resultById[i.simulation_id] }));
  for (const r of results) {
    if (!closedQueue.some(i => i.simulation_id === r.simulation_id)) {
      closed.push({ simulation_id: r.simulation_id, symbol: r.symbol, status: 'CLOSED', result: r });
    }
  }

  let html = `<div class="sim-head">
    <div class="page-title">模拟验证</div>
    <div class="sim-counts">
      <span class="count-chip">等待入场 <b class="warning">${waiting.length}</b></span>
      <span class="count-chip">运行中 <b class="accent">${running.length}</b></span>
      <span class="count-chip">已结束 <b>${closed.length}</b></span>
    </div>
  </div>`;

  // §33 五个标签页
  const tabs = [
    ['waiting', '等待入场'], ['running', '运行中'], ['closed', '已结束'],
    ['stats', '统计'], ['replay', '历史回放'],
  ];
  html += '<div class="tab-bar">';
  for (const [key, label] of tabs) {
    html += `<div class="tab-btn ${State.simTab === key ? 'active' : ''}" onclick="setSimTab('${key}')">${label}</div>`;
  }
  html += '</div>';

  switch (State.simTab) {
    case 'running': html += renderRunningTab(running); break;
    case 'closed': html += renderClosedTab(closed); break;
    case 'stats': html += renderStatsTab(); break;
    case 'replay': html += renderReplayTab(closed); break;
    default: html += renderWaitingTab(waiting);
  }

  view.innerHTML = html;

  // 统计页数据按需拉取
  if (State.simTab === 'stats' && !State.statisticsData) {
    refreshStatistics();
  }
}

function setSimTab(key) {
  State.simTab = key;
  if (key === 'stats' && !State.statisticsData) refreshStatistics();
  renderPage();
}

// §34 等待入场列表
function renderWaitingTab(items) {
  if (items.length === 0) {
    return `<div class="empty-state compact"><div class="icon">📭</div><div class="title">暂无等待入场</div><div class="desc">正式推荐会自动生成快照并进入模拟队列（§22）。</div></div>`;
  }
  let html = `<div class="table-wrap"><table><thead><tr>
    <th>Symbol</th><th>Setup</th><th>方向</th><th>推荐时间</th><th>推荐价</th>
    <th>参考关注区</th><th>当前价</th><th>距离关注区</th><th>机会分</th><th>确认度</th><th>当前状态</th>
  </tr></thead><tbody>`;
  for (const i of items) {
    const snap = i.snapshot || {};
    const dirLabel = snap.direction === 'SHORT' ? '做空' : snap.direction === 'LONG' ? '做多' : '—';
    html += `<tr class="clickable" onclick="openSimulation('${i.simulation_id}')">
      <td><span style="font-weight:600;color:var(--accent)">${i.symbol}</span></td>
      <td>${escapeHtml(i.snapshot && i.snapshot.setup_type || '—')}</td>
      <td>${dirLabel}</td>
      <td class="text-muted">${ts(i.created_at)}</td>
      <td class="num">${fmtPrice(i.recommendation_price)}</td>
      <td class="num">${i.entry_zone_low != null ? fmtPrice(i.entry_zone_low) + '~' + fmtPrice(i.entry_zone_high) : '—'}</td>
      <td class="num font-mono">${fmtPrice(i.current_price)}</td>
      <td class="num ${(i.distance_pct || 0) > 0 ? 'text-short' : 'text-long'}">${i.distance_pct != null ? fmt(i.distance_pct, 2) + '%' : '—'}</td>
      <td class="num">${snap.opportunity_score != null ? fmt(snap.opportunity_score, 1) : '—'}</td>
      <td class="num">${snap.signal_confirmation != null ? fmt(snap.signal_confirmation, 0) + '%' : '—'}</td>
      <td><span class="status-cell sim-${i.status.toLowerCase()}">${SIM_STATUS_LABELS[i.status] || i.status}</span></td>
    </tr>`;
  }
  return html + '</tbody></table></div>';
}

// §35 运行中模拟仓位（资金变化/撤离风险 非持久化 → —）
function renderRunningTab(items) {
  if (items.length === 0) {
    return `<div class="empty-state compact"><div class="icon">📈</div><div class="title">暂无运行中模拟</div></div>`;
  }
  let html = `<div class="table-wrap"><table><thead><tr>
    <th>Symbol</th><th>方向</th><th>Entry</th><th>Current</th><th>PnL</th><th>MFE</th><th>MAE</th>
    <th>TP1</th><th>Invalidation</th><th>当前状态</th><th>资金变化</th><th>撤离风险</th>
  </tr></thead><tbody>`;
  for (const p of items) {
    const dirLabel = p.direction === 'SHORT' ? '做空' : p.direction === 'LONG' ? '做多' : '—';
    const pnl = p.current_pnl_pct;
    html += `<tr class="clickable" onclick="openSimulation('${p.simulation_id}')">
      <td><span style="font-weight:600;color:var(--accent)">${p.symbol}</span></td>
      <td>${dirLabel}</td>
      <td class="num font-mono">${fmtPrice(p.entry_price)}</td>
      <td class="num font-mono">${fmtPrice(p.current_price)}</td>
      <td class="num ${pnl == null || pnl >= 0 ? 'text-long' : 'text-short'}">${pnl != null ? fmtPct(pnl) : '—'}</td>
      <td class="num text-long">${p.mfe_pct != null ? fmtPct(p.mfe_pct) : '—'}</td>
      <td class="num text-short">${p.mae_pct != null ? fmtPct(p.mae_pct) : '—'}</td>
      <td class="num">${fmtPrice(p.tp1)}</td>
      <td class="num">${fmtPrice(p.stop_price)}</td>
      <td><span class="status-cell sim-open">模拟跟踪中</span></td>
      <td class="text-muted">—</td>
      <td class="text-muted">—</td>
    </tr>`;
  }
  return html + '</tbody></table></div>';
}

// §36 已结束
function renderClosedTab(items) {
  if (items.length === 0) {
    return `<div class="empty-state compact"><div class="icon">📦</div><div class="title">暂无已结束模拟</div></div>`;
  }
  let html = `<div class="table-wrap"><table><thead><tr>
    <th>Symbol</th><th>Setup</th><th>方向</th><th>Entry</th><th>Exit</th><th>Result</th>
    <th>Exit Reason</th><th>MFE</th><th>MAE</th><th>Duration</th><th>Opportunity</th><th>Confirmation</th>
  </tr></thead><tbody>`;
  const sorted = [...items].sort((a, b) => ((b.result && b.result.exit_time) || 0) - ((a.result && a.result.exit_time) || 0));
  for (const it of sorted) {
    const r = it.result || {};
    const snap = it.snapshot || {};
    const dirLabel = (snap.direction || r.direction) === 'SHORT' ? '做空' : (snap.direction || r.direction) === 'LONG' ? '做多' : '—';
    const pnl = r.pnl_pct;
    const reasonLabel = EXIT_REASON_LABELS[r.exit_reason] || it.exit_reason || (SIM_STATUS_LABELS[it.status] || it.status);
    html += `<tr class="clickable" onclick="openSimulation('${it.simulation_id}')">
      <td><span style="font-weight:600;color:var(--accent)">${it.symbol}</span></td>
      <td>${escapeHtml(snap.setup_type || '—')}</td>
      <td>${dirLabel}</td>
      <td class="num font-mono">${fmtPrice(r.entry_price != null ? r.entry_price : it.entry_price)}</td>
      <td class="num font-mono">${fmtPrice(r.exit_price != null ? r.exit_price : it.exit_price)}</td>
      <td class="num ${pnl == null || pnl >= 0 ? 'text-long' : 'text-short'}">${pnl != null ? fmtPct(pnl) : '—'}</td>
      <td>${escapeHtml(reasonLabel)}</td>
      <td class="num text-long">${r.mfe_pct != null ? fmtPct(r.mfe_pct) : '—'}</td>
      <td class="num text-short">${r.mae_pct != null ? fmtPct(r.mae_pct) : '—'}</td>
      <td class="text-muted">${fmtDurHours(r.duration_hours)}</td>
      <td class="num">${snap.opportunity_score != null ? fmt(snap.opportunity_score, 1) : '—'}</td>
      <td class="num">${snap.signal_confirmation != null ? fmt(snap.signal_confirmation, 0) + '%' : '—'}</td>
    </tr>`;
  }
  return html + '</tbody></table></div>';
}

// §37 统计（至少 10 项）+ §38 分桶 + §39 Setup 统计
function renderStatsTab() {
  const st = State.statisticsData;
  if (!st) {
    return `<div class="empty-state compact"><div class="icon">📊</div><div class="title">统计加载中</div><div class="desc">正在汇总模拟结果<span class="dots"></span></div></div>`;
  }
  const ov = st.overview || {};
  let html = `<div class="stats-grid">`;
  const rows = [
    ['推荐次数', ov.recommendations], ['进入观察区', ov.zone_reached],
    ['通过 Revalidation', ov.revalidation_passed], ['模拟入场', ov.entries],
    ['TP1 次数', ov.tp1_hit], ['TP2 次数', ov.tp2_hit],
    ['失效次数', ov.invalidation], ['撤离退出', ov.withdrawal_exit],
    ['平均 MFE', ov.avg_mfe_pct != null ? fmtPct(ov.avg_mfe_pct) : '—'],
    ['平均 MAE', ov.avg_mae_pct != null ? fmtPct(ov.avg_mae_pct) : '—'],
  ];
  for (const [label, v] of rows) {
    html += `<div class="stat-tile"><span class="label">${label}</span><span class="value">${v != null ? v : 0}</span></div>`;
  }
  html += '</div>';

  // §38 分桶（默认展示 Opportunity + Setup Type）
  const buckets = st.buckets || {};
  html += `<div class="section-title" style="margin-top:16px">分桶统计</div>`;
  const bandTables = [
    ['opportunity_score', 'Opportunity Score'],
    ['signal_confirmation', 'Signal Confirmation'],
    ['setup_type', 'Setup Type'],
    ['market_regime', 'Market Regime'],
    ['direction', 'Direction'],
    ['timeframe', 'Timeframe'],
  ];
  for (const [key, label] of bandTables) {
    const b = buckets[key] || {};
    const keys = Object.keys(b);
    if (keys.length === 0) continue;
    html += `<div class="bucket-block"><div class="bucket-title">${label}</div><div class="table-wrap"><table><thead><tr><th>区间</th><th>样本</th><th>${key.includes('setup') || key === 'market_regime' || key === 'direction' || key === 'timeframe' ? '累计收益' : '平均收益'}</th></tr></thead><tbody>`;
    for (const k of keys) {
      const d = b[k];
      const cum = d.cum_pnl_pct != null;
      const v = cum ? d.cum_pnl_pct : d.avg_pnl_pct;
      html += `<tr><td>${escapeHtml(k)}</td><td class="num">${d.count}</td><td class="num ${v >= 0 ? 'text-long' : 'text-short'}">${fmtPct(v)}</td></tr>`;
    }
    html += '</tbody></table></div></div>';
  }

  // §39 Setup 统计（推荐数 → 入场数 → 转化率）
  const conv = st.setup_conversion || {};
  const convKeys = Object.keys(conv);
  if (convKeys.length > 0) {
    html += `<div class="section-title" style="margin-top:16px">Setup 统计</div><div class="table-wrap"><table><thead><tr><th>Setup</th><th>推荐</th><th>入场</th><th>转化率</th></tr></thead><tbody>`;
    for (const k of convKeys) {
      const d = conv[k];
      html += `<tr><td>${escapeHtml(k)}</td><td class="num">${d.recommended}</td><td class="num">${d.entered}</td><td class="num">${fmt(d.conversion_rate * 100, 1)}%</td></tr>`;
    }
    html += '</tbody></table></div>';
  }
  return html;
}

// §71 历史回放：推荐快照 → 价格 → 入场 → 退出 → 固定 TP/Stop 对比
function renderReplayTab(items) {
  if (items.length === 0) {
    return `<div class="empty-state compact"><div class="icon">🎞️</div><div class="title">暂无历史回放</div><div class="desc">已结束的模拟会在这里展示完整闭环（发现 → 监督 → 推荐 → 验证 → 跟踪 → 退出 → 统计）。</div></div>`;
  }
  const sorted = [...items].sort((a, b) => ((b.result && b.result.exit_time) || 0) - ((a.result && a.result.exit_time) || 0));
  let html = '<div class="card-grid">';
  for (const it of sorted) {
    const r = it.result || {};
    const snap = it.snapshot || {};
    const pnl = r.pnl_pct;
    const staticPlan = r.static_plan_result || {};
    const staticPnl = staticPlan.static_pnl_pct;
    const reasonLabel = EXIT_REASON_LABELS[r.exit_reason] || it.exit_reason || '—';
    html += `<div class="card replay-card" onclick="openSimulation('${it.simulation_id}')">
      <div class="card-header">
        <div style="display:flex;align-items:center;gap:8px">
          <span class="card-symbol">${it.symbol}</span>
          <span class="text-muted" style="font-size:0.7rem">${it.simulation_id}</span>
        </div>
        <span class="badge badge-setup">${escapeHtml(snap.setup_type || '—')}</span>
      </div>
      <div class="replay-chain">
        <div><span class="label">推荐快照</span><span class="value">${tsFull(it.created_at)} · ${fmtPrice(snap.current_price)} · 机会 ${snap.opportunity_score != null ? fmt(snap.opportunity_score, 0) : '—'}</span></div>
        <div><span class="label">观察 → 入场</span><span class="value">${it.entry_zone_low != null ? fmtPrice(it.entry_zone_low) + '~' + fmtPrice(it.entry_zone_high) : '—'} → ${fmtPrice(r.entry_price)} <span class="text-muted">(${escapeHtml((it.entry_reason || '').slice(0, 30))})</span></span></div>
        <div><span class="label">退出原因</span><span class="value">${escapeHtml(reasonLabel)} @ ${fmtPrice(r.exit_price)} ${r.duration_hours != null ? '· ' + fmtDurHours(r.duration_hours) : ''}</span></div>
        <div><span class="label">动态结果</span><span class="value ${pnl == null || pnl >= 0 ? 'text-long' : 'text-short'}">${pnl != null ? fmtPct(pnl) : '—'} <span class="text-muted">MFE ${r.mfe_pct != null ? fmtPct(r.mfe_pct) : '—'} / MAE ${r.mae_pct != null ? fmtPct(r.mae_pct) : '—'}</span></span></div>
        <div><span class="label">固定 TP/Stop</span><span class="value ${staticPnl == null || staticPnl >= 0 ? 'text-long' : 'text-short'}">${staticPlan.outcome ? escapeHtml(EXIT_REASON_LABELS[staticPlan.outcome] || staticPlan.outcome) + ' ' + (staticPnl != null ? fmtPct(staticPnl) : '') : '—'}</span></div>
      </div>
    </div>`;
  }
  return html + '</div>';
}

// ═══════════════════════════════════════
// 数据健康
// ═══════════════════════════════════════
function renderHealth(view) {
  const health = State.healthData || [];
  const coverage = State.homeData && State.homeData.health;

  if (!health || health.length === 0) {
    view.innerHTML = `
      <div class="empty-state">
        <div class="icon">⚙️</div>
        <div class="title">数据健康信息加载中</div>
        <div class="desc">正在采集各数据流的状态信息<span class="dots"></span></div>
      </div>`;
    return;
  }

  let html = '<div class="page-title">数据健康</div>';

  // 覆盖率（§46）
  if (coverage) {
    html += `<div class="coverage-bar">
      <span class="label">总体覆盖率：</span>
      <span class="value accent">${coverage.coverage_pct != null ? fmt(coverage.coverage_pct, 1) + '%' : '—'}</span>
      <span class="sub">${escapeHtml(coverage.level_label || '')} · 健康配对 ${coverage.healthy_pairs}/${coverage.total_pairs}</span>
      ${coverage.critical_stream_down ? '<span class="critical">⚠ 核心流中断</span>' : ''}
    </div>`;
  }

  const streamLabels = { aggTrade: 'AggTrade', kline: 'Kline', oi_poller: 'OI', funding_premium: 'Funding' };

  html += `<div class="table-wrap"><table class="health-table">
    <thead><tr>
      <th>交易对</th><th>AggTrade</th><th>Kline</th><th>OI</th><th>Funding</th><th>置信度</th>
    </tr></thead><tbody>`;
  for (const row of health) {
    html += `<tr><td><span style="font-weight:600;color:var(--accent)">${row.symbol}</span></td>`;
    for (const prefix of ['aggTrade', 'kline', 'oi_poller', 'funding_premium']) {
      const stream = row[prefix];
      if (stream) {
        const status = stream.status || 'FAIL';
        const statusLabel = stream.status_label || status;
        html += `<td><span class="status-cell health-${status}"><span class="dot"></span>${escapeHtml(statusLabel)}</span></td>`;
      } else {
        html += '<td>-</td>';
      }
    }
    const conf = row.confidence_state || 'UNKNOWN';
    const confLabel = row.confidence_state_label || conf;
    html += `<td style="font-weight:600">${escapeHtml(confLabel)}</td></tr>`;
  }
  html += '</tbody></table></div>';
  view.innerHTML = html;
}

// ═══════════════════════════════════════
// Drawer（§17 A-I / §51 / §55 / §56）
// ═══════════════════════════════════════
function selectSymbol(symbol) {
  openDrawer(symbol);
}
async function openDrawer(symbol) {
  State.selectedSymbol = symbol;
  const drawer = document.getElementById('side-drawer');
  const overlay = document.getElementById('drawer-overlay');
  if (!drawer) return;
  drawer.classList.add('open');
  if (overlay) overlay.classList.add('show');
  drawer.innerHTML = '<div class="drawer-loading">加载中<span class="dots"></span></div>';
  const detail = await API.getSymbolDetail(symbol);
  if (!detail || detail.error) {
    drawer.innerHTML = '<div class="drawer-loading">数据不足</div>';
    return;
  }
  drawer.innerHTML = renderDrawer(detail);
}
async function openSimulation(simulationId) {
  const drawer = document.getElementById('side-drawer');
  const overlay = document.getElementById('drawer-overlay');
  if (!drawer) return;
  drawer.classList.add('open');
  if (overlay) overlay.classList.add('show');
  drawer.innerHTML = '<div class="drawer-loading">加载模拟详情<span class="dots"></span></div>';
  const data = await API.getSimulation(simulationId);
  if (!data) {
    drawer.innerHTML = '<div class="drawer-loading">数据不足</div>';
    return;
  }
  drawer.innerHTML = renderSimulationDrawer(data);
}
function closeDrawer() {
  const drawer = document.getElementById('side-drawer');
  const overlay = document.getElementById('drawer-overlay');
  if (drawer) drawer.classList.remove('open');
  if (overlay) overlay.classList.remove('show');
  State.selectedSymbol = null;
}

// §17 A-I Drawer
function renderDrawer(d) {
  const dir = d.direction || '';
  const dec = d.decision_snapshot || {};
  const frozen = dec.decision || {};
  const frozenAt = dec.frozen_at;
  const opp = d.opportunity_score;
  const sc = d.signal_confirmation_pct;
  const dc = d.data_confidence_pct;
  const fOpp = frozen.opportunity_score;
  const fSc = frozen.signal_confirmation;
  const fDc = frozen.data_confidence;
  let html = '';

  // A. 当前结论（§17 A / §55）
  html += `<div class="drawer-section">
    <div class="drawer-header">
      <span class="drawer-symbol">${d.symbol}</span>
      <button class="drawer-close" onclick="closeDrawer()">✕</button>
    </div>
    <div class="drawer-price-row">
      <span class="font-mono" style="font-size:1.2rem;font-weight:700" data-price-symbol="${d.symbol}">${fmtPrice(State.pricesData[d.symbol] || d.current_price)}</span>
      <span class="${pctColor(d.price_change_24h)}">${fmtPct(d.price_change_24h)}</span>
    </div>
    <div class="drawer-badges">
      <span class="badge badge-state-${d.state}">${escapeHtml(d.state_display || d.state_label || d.state)}</span>
      ${dir ? `<span class="badge badge-${dir.toLowerCase()}">${escapeHtml(d.direction_label || dir)}</span>` : ''}
      ${d.setup_label ? `<span class="badge badge-setup">${escapeHtml(d.setup_label)}</span>` : ''}
    </div>
    <div class="drawer-tp">
      <div><span class="text-muted">主周期</span> <span class="value">${dash(frozen.primary_timeframe)}</span></div>
      <div><span class="text-muted">快照时间</span> <span class="value font-mono">${ts(frozenAt)} ${ago(frozenAt)}</span></div>
    </div>
  </div>`;

  // B. 核心评分（§17 B / §56 双值：推荐时 vs 当前）
  html += `<div class="drawer-section">
    <div class="drawer-title">核心评分 <span class="dual-hint">推荐时 → 当前</span></div>
    <div class="dual-scores">
      <div><span class="label">机会分</span>
        <span class="rec">${fOpp != null ? fmt(fOpp, 1) : '—'}</span>
        <span class="arrow">→</span>
        <span class="cur text-accent">${opp != null ? fmt(opp, 1) : '—'}</span>
        <span class="arrow ${arrowClass(trendArrow(opp, fOpp))}">${trendArrow(opp, fOpp)}</span>
      </div>
      <div><span class="label">信号确认</span>
        <span class="rec">${fSc != null ? fmt(fSc, 0) + '%' : '—'}</span>
        <span class="arrow">→</span>
        <span class="cur">${sc != null ? fmt(sc, 0) + '%' : '—'}</span>
        <span class="arrow ${arrowClass(trendArrow(sc, fSc))}">${trendArrow(sc, fSc)}</span>
      </div>
      <div><span class="label">数据可信</span>
        <span class="rec">${fDc != null ? fmt(fDc, 0) + '%' : '—'}</span>
        <span class="arrow">→</span>
        <span class="cur">${dc != null ? fmt(dc, 0) + '%' : '—'}</span>
        <span class="arrow ${arrowClass(trendArrow(dc, fDc))}">${trendArrow(dc, fDc)}</span>
      </div>
    </div>
  </div>`;

  // C. 当前计划（§17 C / §18：仅正式状态显示正式计划）
  const plan = d.trade_plan;
  const isFormal = d.state === 'START_CONFIRMED' || d.state === 'CONTINUATION';
  if (plan && isFormal && plan.status === 'ACTIVE') {
    html += `<div class="drawer-section">
      <div class="drawer-title">当前计划 ${plan.frozen ? '🔒 已冻结' : ''} <span class="text-muted" style="font-size:0.7rem">V${plan.version || 0}</span></div>
      <div class="drawer-tp">
        <div><span class="text-muted">参考关注区</span> <span class="font-mono">${plan.reference_entry_low != null ? fmtPrice(plan.reference_entry_low) : '-'} ~ ${plan.reference_entry_high != null ? fmtPrice(plan.reference_entry_high) : '-'}</span></div>
        <div><span class="text-muted">结构失效位</span> <span class="font-mono text-short">${plan.invalidation_price != null ? fmtPrice(plan.invalidation_price) : '-'}</span></div>
        <div><span class="text-muted">TP1</span> <span class="font-mono text-long">${plan.tp1 != null ? fmtPrice(plan.tp1) : '-'} (${plan.rr_tp1 != null ? fmt(plan.rr_tp1, 1) + 'R' : '-'})</span></div>
        <div><span class="text-muted">TP2</span> <span class="font-mono text-long">${plan.tp2 != null ? fmtPrice(plan.tp2) : '-'} (${plan.rr_tp2 != null ? fmt(plan.rr_tp2, 1) + 'R' : '-'})</span></div>
        <div><span class="text-muted">TP3</span> <span class="font-mono text-long">${plan.tp3 != null ? fmtPrice(plan.tp3) : '-'} (${plan.rr_tp3 != null ? fmt(plan.rr_tp3, 1) + 'R' : '-'})</span></div>
        <div><span class="text-muted">建议追</span> <span class="value ${plan.chase_status === 'ok' ? 'long' : 'warn'}">${plan.chase_status === 'ok' ? '可以' : plan.chase_status === 'no_plan' ? '—' : '不建议'}</span></div>
      </div>
      <div class="drawer-plan-reason ${plan.chase_status !== 'ok' ? 'warn' : ''}">${escapeHtml(plan.plan_reason || '')}</div>
    </div>`;
  } else if (d.state === 'SUSPECTED_START' || d.state === 'ACCUMULATION' || d.state === 'RETEST_PENDING') {
    html += `<div class="drawer-section">
      <div class="drawer-title">当前计划</div>
      <div class="drawer-plan-reason warn">候选预案，尚未确认</div>
    </div>`;
  }

  // D. 生命周期（§17 D：发现异动→疑似启动→确认启动→趋势延续→衰竭→撤离）
  const stages = [
    ['ANOMALY', '发现异动'], ['SUSPECTED_START', '疑似启动'], ['START_CONFIRMED', '确认启动'],
    ['CONTINUATION', '趋势延续'], ['EXHAUSTION', '衰竭'], ['WITHDRAWAL', '撤离'],
  ];
  const curIdx = stages.findIndex(s => s[0] === d.state);
  let lifeHtml = '';
  for (let i = 0; i < stages.length; i++) {
    const [key, label] = stages[i];
    const cls = i === curIdx ? 'current' : (curIdx >= 0 && i < curIdx ? 'done' : 'upcoming');
    lifeHtml += `<div class="life-node ${cls}"><span class="dot"></span><span class="label">${label}</span>${i < stages.length - 1 ? '<span class="line"></span>' : ''}</div>`;
  }
  html += `<div class="drawer-section"><div class="drawer-title">生命周期</div><div class="lifecycle">${lifeHtml}</div></div>`;

  // E. 评分明细（§17 E）
  if (d.score_breakdown && d.score_breakdown.subscores) {
    const labels = d.subscore_labels || {};
    html += `<div class="drawer-section"><div class="drawer-title">评分明细</div>`;
    for (const [key, ss] of Object.entries(d.score_breakdown.subscores)) {
      if (ss.is_risk) continue;
      const label = labels[key] || key;
      const color = scoreColor(ss.score, false);
      html += `<div class="score-bar"><span class="name" style="width:90px">${label}</span><div class="track"><div class="fill ${color}" style="width:${ss.score}%"></div></div><span class="num">${ss.available ? fmt(ss.score, 0) : '—'}</span></div>`;
    }
    html += `<div style="font-size:0.7rem;color:var(--text-muted);margin:6px 0 2px">风险</div>`;
    for (const [key, ss] of Object.entries(d.score_breakdown.subscores)) {
      if (!ss.is_risk) continue;
      const label = labels[key] || key;
      const color = scoreColor(ss.score, true);
      html += `<div class="score-bar"><span class="name" style="width:90px">${label}</span><div class="track"><div class="fill ${color}" style="width:${ss.score}%"></div></div><span class="num">${ss.available ? fmt(ss.score, 0) : '—'}</span></div>`;
    }
    html += '</div>';
  }

  // F. 资金摘要（§17 F：RVOL/Taker B/S/Delta/CVD/OI 5m/OI 15m/OI 1h/Funding/Premium/Spot CVD/Perp CVD）
  const fv = d.features || {};
  html += `<div class="drawer-section"><div class="drawer-title">资金摘要</div><div class="drawer-flow">`;
  const flowItems = [
    ['RVOL', fv.relative_volume, 'x'],
    ['Taker B/S', fv.taker_bs, ''],
    ['Delta', fv.signed_delta, ''],
    ['CVD', fv.cvd, ''],
    ['OI 5m', fv.oi_change_pct_5m != null ? fv.oi_change_pct_5m : fv.oi_change_5m, '%'],
    ['OI 15m', fv.oi_change_pct_15m != null ? fv.oi_change_pct_15m : fv.oi_change_15m, '%'],
    ['OI 1h', fv.oi_change_pct_1h != null ? fv.oi_change_pct_1h : fv.oi_change_1h, '%'],
    ['Funding', fv.funding, '%'],
    ['Premium', fv.premium, ''],
    ['Spot CVD', fv.spot_cvd, ''],
    ['Perp CVD', fv.cvd, ''],
  ];
  for (const [label, val, unit] of flowItems) {
    if (val != null) html += `<div class="flow-item"><span class="text-muted">${label}</span> <span class="font-mono">${fmt(val, 4)}${unit}</span></div>`;
  }
  if (d.spot_perp_label) html += `<div class="flow-item full"><span class="text-muted">现货×合约</span> ${escapeHtml(d.spot_perp_label)}</div>`;
  if (d.impulse_label) html += `<div class="flow-item full"><span class="text-muted">多空推动</span> ${escapeHtml(d.impulse_label)}</div>`;
  html += `</div></div>`;

  // G. Breakout Lifecycle（§17 G）
  const bo = d.breakout_state || {};
  const st = d.structure_state || {};
  html += `<div class="drawer-section"><div class="drawer-title">突破生命周期</div><div class="drawer-tp">`;
  html += `<div><span class="text-muted">突破时间</span> <span class="value font-mono">${ts(bo.breakout_time)}</span></div>`;
  html += `<div><span class="text-muted">突破位</span> <span class="value font-mono">${bo.breakout_level != null ? fmtPrice(bo.breakout_level) : '—'}</span></div>`;
  html += `<div><span class="text-muted">突破保持</span> <span class="value">${bo.time_above_level_ms != null ? fmtDurHours(bo.time_above_level_ms / 3_600_000) : '—'} ${bo.breakout_hold ? '<span class="badge badge-setup">保持</span>' : ''}</span></div>`;
  html += `<div><span class="text-muted">回撤深度</span> <span class="value">${bo.max_retrace != null ? fmt(bo.max_retrace * 100, 2) + '%' : '—'}</span></div>`;
  html += `<div><span class="text-muted">首次回踩</span> <span class="value">${bo.retest_started ? (bo.retest_confirmed ? '已确认' : '进行中') : '—'}${bo.retest_depth != null ? ' 深 ' + fmt(bo.retest_depth * 100, 2) + '%' : ''}</span></div>`;
  html += `<div><span class="text-muted">二次确认</span> <span class="value">${bo.retest_confirmed ? '✅ 已确认' : '—'}${bo.strong_confirm ? ' <span class="badge badge-setup">强确认</span>' : ''}</span></div>`;
  html += `<div><span class="text-muted">距局部高点</span> <span class="value">${st.local_high && d.current_price ? fmt((d.current_price / st.local_high - 1) * 100, 2) + '%' : '—'}</span></div>`;
  html += '</div></div>';

  // H. Evidence / Veto（§17 H）
  if (d.signal_confirmation_breakdown) {
    const sb = d.signal_confirmation_breakdown;
    html += `<div class="drawer-section"><div class="drawer-title">Evidence / Veto</div>`;
    html += `<div class="evidence-vote">核心证据 ${sb.core_passed}/${sb.core_total} · 辅助证据 ${sb.supporting_passed}/${sb.supporting_total} · ${sb.veto_passed ? '✅ Veto通过' : '❌ Veto命中'}</div>`;
    if (sb.strong_confirm) html += `<div class="strong-confirm">确认强度：强</div>`;
    html += '</div>';
  }

  // I. 模拟状态（§17 I / §51）
  if (d.simulation && d.simulation.length > 0) {
    html += renderDrawerSimulation(d.simulation, d);
  }

  return html;
}

// §51 Drawer 模拟状态：等待回踩（快照信息）或已入场（持仓信息）
function renderDrawerSimulation(sims, d) {
  let html = `<div class="drawer-section"><div class="drawer-title">模拟状态</div>`;
  const stageOrder = { WATCHING: 0, ENTRY_ZONE_REACHED: 1, REVALIDATING: 2, ARMED: 3, SIMULATED_ENTRY: 4, OPEN: 5 };
  const sorted = [...sims].sort((a, b) => (stageOrder[(a.item && a.item.status) || ''] || 9) - (stageOrder[(b.item && b.item.status) || ''] || 9));
  const sim = sorted[0];
  if (!sim || !sim.item) return '</div>';
  const item = sim.item;
  const pos = sim.position;
  const res = sim.result;
  const snap = item.snapshot || {};

  if (pos) {
    // 已模拟入场
    const pnl = pos.current_pnl_pct;
    const onHold = pos.status === 'OPEN';
    html += `<div class="sim-block ${onHold ? 'live' : ''}">
      <div class="sim-title">${SIM_STATUS_LABELS[pos.status === 'OPEN' ? 'OPEN' : 'SIMULATED_ENTRY'] || '已模拟入场'} ${onHold ? '<span class="badge badge-setup">持仓中</span>' : ''}</div>
      <div class="drawer-tp">
        <div><span class="text-muted">Entry</span> <span class="value font-mono">${fmtPrice(pos.entry_price)} (${ts(pos.entry_time)})</span></div>
        <div><span class="text-muted">Current</span> <span class="value font-mono">${fmtPrice(pos.current_price)}</span></div>
        <div><span class="text-muted">PnL</span> <span class="value ${pnl == null || pnl >= 0 ? 'long' : 'short'}">${pnl != null ? fmtPct(pnl) : '—'}</span></div>
        <div><span class="text-muted">MFE</span> <span class="value long">${pos.mfe_pct != null ? fmtPct(pos.mfe_pct) : '—'}</span></div>
        <div><span class="text-muted">MAE</span> <span class="value short">${pos.mae_pct != null ? fmtPct(pos.mae_pct) : '—'}</span></div>
        <div><span class="text-muted">Exit Trigger</span> <span class="value">${pos.exit_reason ? escapeHtml(EXIT_REASON_LABELS[pos.exit_reason] || pos.exit_reason) : (pos.tp1 != null ? 'TP1 ' + fmtPrice(pos.tp1) : '') + (pos.stop_price != null ? ' / Stop ' + fmtPrice(pos.stop_price) : '')}</span></div>
      </div>
    </div>`;
  } else {
    // 等待回踩（§51）
    const dist = item.distance_pct;
    html += `<div class="sim-block">
      <div class="sim-title">等待回踩</div>
      <div class="drawer-tp">
        <div><span class="text-muted">快照时间</span> <span class="value font-mono">${ts(item.created_at)}</span></div>
        <div><span class="text-muted">快照价格</span> <span class="value font-mono">${fmtPrice(item.recommendation_price)}</span></div>
        <div><span class="text-muted">参考关注</span> <span class="value font-mono">${item.entry_zone_low != null ? fmtPrice(item.entry_zone_low) + '~' + fmtPrice(item.entry_zone_high) : '—'}</span></div>
        <div><span class="text-muted">当前价</span> <span class="value font-mono">${fmtPrice(item.current_price)}</span></div>
        <div><span class="text-muted">距离关注区</span> <span class="value ${dist != null && dist > 0 ? 'short' : 'long'}">${dist != null ? fmt(dist, 2) + '%' : '—'}</span></div>
        <div><span class="text-muted">状态</span> <span class="value">${SIM_STATUS_LABELS[item.status] || item.status}</span></div>
      </div>
    </div>`;
  }
  if (res) {
    const pnl = res.pnl_pct;
    html += `<div class="sim-block result">
      <div class="sim-title">最终结果</div>
      <div class="drawer-tp">
        <div><span class="text-muted">退出原因</span> <span class="value">${escapeHtml(EXIT_REASON_LABELS[res.exit_reason] || res.exit_reason || '—')}</span></div>
        <div><span class="text-muted">退出价</span> <span class="value font-mono">${fmtPrice(res.exit_price)}</span></div>
        <div><span class="text-muted">结果</span> <span class="value ${pnl == null || pnl >= 0 ? 'long' : 'short'}">${pnl != null ? fmtPct(pnl) : '—'}</span></div>
      </div>
    </div>`;
  }
  return html + '</div>';
}

// §71 模拟详情 Drawer：完整闭环
function renderSimulationDrawer(data) {
  const item = data.item || {};
  const pos = data.position;
  const res = data.result;
  const events = data.events || [];
  const snap = item.snapshot || {};
  const pnl = res && res.pnl_pct;

  let html = `<div class="drawer-section">
    <div class="drawer-header">
      <span class="drawer-symbol">${item.symbol}</span>
      <span class="badge badge-setup">${escapeHtml(snap.setup_type || '—')}</span>
      <button class="drawer-close" onclick="closeDrawer()">✕</button>
    </div>
    <div class="drawer-badges">
      <span class="status-cell sim-${(item.status || '').toLowerCase()}">${SIM_STATUS_LABELS[item.status] || item.status}</span>
      <span class="text-muted">${item.simulation_id}</span>
    </div>
  </div>`;

  html += `<div class="drawer-section"><div class="drawer-title">推荐快照</div><div class="drawer-tp">
    <div><span class="text-muted">时间</span> <span class="value font-mono">${tsFull(item.created_at)}</span></div>
    <div><span class="text-muted">推荐价</span> <span class="value font-mono">${fmtPrice(item.recommendation_price)}</span></div>
    <div><span class="text-muted">机会分</span> <span class="value">${snap.opportunity_score != null ? fmt(snap.opportunity_score, 1) : '—'}</span></div>
    <div><span class="text-muted">确认度</span> <span class="value">${snap.signal_confirmation != null ? fmt(snap.signal_confirmation, 0) + '%' : '—'}</span></div>
    <div><span class="text-muted">数据可信</span> <span class="value">${snap.data_confidence != null ? fmt(snap.data_confidence, 0) + '%' : '—'}</span></div>
    <div><span class="text-muted">市场背景</span> <span class="value">${snap.market_regime ? escapeHtml(snap.market_regime.label || snap.market_regime.regime || '—') : '—'}</span></div>
  </div></div>`;

  if (pos) {
    html += `<div class="drawer-section"><div class="drawer-title">模拟持仓</div><div class="drawer-tp">
      <div><span class="text-muted">方向</span> <span class="value">${pos.direction === 'SHORT' ? '做空' : pos.direction === 'LONG' ? '做多' : '—'}</span></div>
      <div><span class="text-muted">Entry</span> <span class="value font-mono">${fmtPrice(pos.entry_price)} (${ts(pos.entry_time)})</span></div>
      <div><span class="text-muted">入场原因</span> <span class="value">${escapeHtml(pos.entry_reason || item.entry_reason || '—')}</span></div>
      <div><span class="text-muted">TP1/TP2/TP3</span> <span class="value font-mono">${fmtPrice(pos.tp1)} / ${fmtPrice(pos.tp2)} / ${fmtPrice(pos.tp3)}</span></div>
      <div><span class="text-muted">Stop（失效位）</span> <span class="value font-mono">${fmtPrice(pos.stop_price)}</span></div>
    </div></div>`;
  }

  // 事件流
  if (events.length > 0) {
    const evSorted = [...events].reverse();
    html += `<div class="drawer-section"><div class="drawer-title">模拟事件流</div><div class="timeline">`;
    for (const ev of evSorted) {
      const from = ev.old_status, to = ev.new_status;
      html += `<div class="timeline-item"><span class="time">${ts(ev.asof)}</span><span class="label">${SIM_STATUS_LABELS[from] || from} → ${SIM_STATUS_LABELS[to] || to}</span><span class="reason">${escapeHtml(ev.reason || '')}</span></div>`;
    }
    html += '</div></div>';
  }

  if (res) {
    const staticPlan = res.static_plan_result || {};
    const staticPnl = staticPlan.static_pnl_pct != null ? staticPlan.static_pnl_pct * 100 : null;
    html += `<div class="drawer-section"><div class="drawer-title">最终结果（动态 vs 固定计划）</div><div class="drawer-tp">
      <div><span class="text-muted">退出原因</span> <span class="value">${escapeHtml(EXIT_REASON_LABELS[res.exit_reason] || res.exit_reason || '—')}</span></div>
      <div><span class="text-muted">Exit</span> <span class="value font-mono">${fmtPrice(res.exit_price)} (${ts(res.exit_time)})</span></div>
      <div><span class="text-muted">动态结果</span> <span class="value ${pnl == null || pnl >= 0 ? 'long' : 'short'}">${pnl != null ? fmtPct(pnl) : '—'}</span></div>
      <div><span class="text-muted">MFE / MAE</span> <span class="value">${res.mfe_pct != null ? fmtPct(res.mfe_pct) : '—'} / ${res.mae_pct != null ? fmtPct(res.mae_pct) : '—'}</span></div>
      <div><span class="text-muted">固定 TP/Stop</span> <span class="value">${staticPlan.outcome ? escapeHtml(EXIT_REASON_LABELS[staticPlan.outcome] || staticPlan.outcome) + ' ' + (staticPnl != null ? fmtPct(staticPnl) : '') : '—'}</span></div>
    </div></div>`;
  }
  return html;
}

// ═══════════════════════════════════════
// 搜索
// ═══════════════════════════════════════
function handleSearch(query) {
  State.searchQuery = query;
  const dropdown = document.getElementById('search-dropdown');
  if (!query || query.length < 1) {
    dropdown.classList.remove('show');
    return;
  }
  const q = query.toUpperCase();
  const results = (State.radarData || []).filter(s => s.symbol.includes(q)).slice(0, 10);
  if (results.length === 0) {
    dropdown.innerHTML = `<div class="search-result"><span class="meta">无匹配结果</span></div>`;
    dropdown.classList.add('show');
    return;
  }
  dropdown.innerHTML = results.map(s => `
    <div class="search-result" onclick="selectSymbol('${s.symbol}');clearSearch()">
      <span class="sym">${s.symbol}</span>
      <span class="meta">${escapeHtml(s.state_label || s.state)} · ${s.opportunity_score != null ? fmt(s.opportunity_score, 0) + '分' : '-'}</span>
    </div>`).join('');
  dropdown.classList.add('show');
}
function clearSearch() {
  const input = document.getElementById('search-input');
  const dropdown = document.getElementById('search-dropdown');
  if (input) input.value = '';
  if (dropdown) dropdown.classList.remove('show');
  State.searchQuery = '';
}

// ═══════════════════════════════════════
// 数据轮询 — V1.3 §12 分层节奏
//   当前价格 3~5s · 资金摘要 10s · 24h涨跌/子评分/Opportunity/确认 30s · Top10 排名 60s
// ═══════════════════════════════════════
let priceTimer = null;
let dataTimer = null;
let slowTimer = null;
let topTimer = null;

// §12 当前价格 3~5s
async function pollPrices() {
  const prices = await API.getPrices();
  if (prices) State.pricesData = prices;
  updatePriceDisplay();
}
function updatePriceDisplay() {
  for (const [sym, price] of Object.entries(State.pricesData)) {
    const el = document.querySelector(`[data-price-symbol="${sym}"]`);
    if (el) el.textContent = fmtPrice(price);
  }
}

// §12 资金摘要 10s（页面数据）
async function pollData() {
  const page = State.currentPage;
  const tasks = [];
  if (page === 'market') {
    tasks.push(API.getMarket().then(d => { if (d) State.marketData = d; }));
    tasks.push(API.getRadar().then(d => { if (d) State.radarData = d; }));
    tasks.push(API.getStats().then(d => { if (d) State.statsData = d; }));
  }
  if (page === 'supervision') {
    tasks.push(API.getSupervision().then(d => { if (d) State.supervisionData = d; }));
  }
  if (page === 'simulations') {
    tasks.push(API.getSimulations().then(d => { if (d) State.simData = d; }));
  }
  if (page === 'health') {
    tasks.push(API.getHealth().then(d => { if (d) State.healthData = d; }));
  }
  if (page === 'home') {
    // §12 资金摘要不在首页展示；首页慢节奏在 pollSlow
  }
  await Promise.all(tasks);
  if (page === 'market' || page === 'supervision' || page === 'simulations' || page === 'health') {
    renderPage();
  }
}

// §12 24h涨跌 / 子评分 / Opportunity / Signal Confirmation 30s
async function pollSlow() {
  const page = State.currentPage;
  if (page === 'home') {
    const [home, stats] = await Promise.all([API.getHome(), API.getStats()]);
    if (home) {
      State.homeData = home;
      State.loading = false;
      if (stats) State.statsData = stats;
      // 先用旧趋势渲染（§54 上一轮 30s 稳定窗口），再存新基线
      renderHomeIfChanged(home);
      captureScoreTrend(home);
    }
  }
  if (page === 'market') {
    const radar = await API.getRadar();
    if (radar) { State.radarData = radar; renderPage(); }
  }
}

// §54 捕获 30s 稳定窗口趋势
function captureScoreTrend(home) {
  const confirmed = home.confirmed_opportunities || [];
  const trend = {};
  for (const s of confirmed) {
    trend[s.symbol] = {
      opp: liveValue(s, 'opportunity_score'),
      sc: liveValue(s, 'signal_confirmation'),
      dc: liveValue(s, 'data_confidence'),
      subs: s.live_subscores || {},
      accum: s.accumulation_score,
    };
  }
  State.scoreTrend = trend;
}

// §12/§66.8 首页不秒级重排：材料变化才重渲染
function homeMateriallyChanged(home) {
  const prev = State.lastRendered.home;
  if (!prev) return true;
  const confirmed = home.confirmed_opportunities || [];
  if (confirmed.length !== prev.confirmed.length) return true;
  const nowWatch = (home.watch_candidates || []).map(w => w.symbol).join(',');
  if (nowWatch !== prev.watchSymbols) return true;
  for (let i = 0; i < confirmed.length; i++) {
    const a = confirmed[i], b = prev.confirmed[i];
    if (!b || a.symbol !== b.symbol) return true;
    if (a.state !== b.state || a.direction !== b.direction) return true;
    const aOpp = snapValue(a, 'opportunity_score'), bOpp = b.opp;
    if (Math.abs((aOpp || 0) - (bOpp || 0)) > 1) return true;
    const aSc = snapValue(a, 'signal_confirmation'), bSc = b.sc;
    if (Math.abs((aSc || 0) - (bSc || 0)) > 1) return true;
  }
  return false;
}
function renderHomeIfChanged(home) {
  if (homeMateriallyChanged(home)) {
    renderPage();
  }
}

// §12 Top10 排名 60s：确认材料变化后重渲染首页
async function pollTop() {
  if (State.currentPage === 'home' && State.homeData) {
    const home = await API.getHome();
    if (home) {
      State.homeData = home;
      State.loading = false;
      renderHomeIfChanged(home);
      captureScoreTrend(home);
    }
  }
}

function startPolling() {
  if (priceTimer) clearInterval(priceTimer);
  if (dataTimer) clearInterval(dataTimer);
  if (slowTimer) clearInterval(slowTimer);
  if (topTimer) clearInterval(topTimer);
  pollPrices();
  pollData();
  pollSlow();
  pollTop();
  priceTimer = setInterval(pollPrices, 4000);    // §12 当前价格 3~5s
  dataTimer = setInterval(pollData, 10000);      // §12 资金摘要 10s
  slowTimer = setInterval(pollSlow, 30000);      // §12 24h/子评分/Opportunity/确认 30s
  topTimer = setInterval(pollTop, 60000);        // §12 Top10 排名 60s
}

async function refreshStatistics() {
  const st = await API.getStatistics();
  if (st) State.statisticsData = st;
  if (State.currentPage === 'simulations' && State.simTab === 'stats') {
    renderPage();
  }
}

// ═══════════════════════════════════════
// 初始化
// ═══════════════════════════════════════
function initApp() {
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => navigate(el.dataset.route));
  });

  const searchInput = document.getElementById('search-input');
  if (searchInput) {
    searchInput.addEventListener('input', e => handleSearch(e.target.value));
    searchInput.addEventListener('focus', e => { if (e.target.value) handleSearch(e.target.value); });
  }

  document.addEventListener('click', e => {
    if (!e.target.closest('.search-box')) {
      const dd = document.getElementById('search-dropdown');
      if (dd) dd.classList.remove('show');
    }
  });

  document.addEventListener('keydown', e => {
    if (e.key === '/' && e.target.tagName !== 'INPUT') {
      e.preventDefault();
      const input = document.getElementById('search-input');
      if (input) input.focus();
    }
    if (e.key === 'Escape') {
      clearSearch();
      const input = document.getElementById('search-input');
      if (input) input.blur();
    }
  });

  window.addEventListener('hashchange', handleRoute);

  startPolling();
  handleRoute();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}