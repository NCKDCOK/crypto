/**
 * 资金行为雷达 V1.1 — 主应用
 * SPA 路由 + 状态管理 + 页面渲染
 * 科技感 + Apple 式克制丝滑
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
  detailMode: 'normal', // normal | pro
  radarData: [],
  statsData: {},
  healthData: [],
  signalsData: [],
  top10Data: [],
  summaryData: {},
  detailCache: {},
  detailSymbol: null,
  loading: true,
  previewSymbol: null,
};

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
  return Math.floor(s / 3600) + 'h前';
}

function ts(ms) {
  if (!ms) return '-';
  return new Date(ms).toLocaleTimeString('zh-CN', { hour12: false });
}

function tsFull(ms) {
  if (!ms) return '-';
  return new Date(ms).toLocaleString('zh-CN', { hour12: false });
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
  if (parts[0] === 'symbol' && parts[1]) return { page: 'detail', symbol: parts[1] };
  if (parts[0] === 'signals') return { page: 'signals' };
  if (parts[0] === 'health') return { page: 'health' };
  if (parts[0] === 'replay') return { page: 'replay' };
  return { page: 'home' };
}

function handleRoute() {
  const route = parseRoute();
  State.currentPage = route.page;
  if (route.symbol) {
    State.detailSymbol = route.symbol;
  }

  // 更新导航高亮
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
    case 'detail': renderDetail(view); break;
    case 'signals': renderSignals(view); break;
    case 'health': renderHealth(view); break;
    case 'replay': renderReplay(view); break;
  }
}

// ═══════════════════════════════════════
// 首页 — Top10 大屏
// ═══════════════════════════════════════
function renderHome(view) {
  const summary = State.summaryData;
  const top10 = State.top10Data;
  const stats = State.statsData;

  // Loading 状态
  if (State.loading) {
    view.innerHTML = `
      <div class="loading-text">
        <div>系统预热中</div>
        <div class="warmup-bar"><div class="fill" style="width:${Math.min((stats.universe_size || 0) > 0 ? 60 : 20, 80)}%"></div></div>
        <div class="text-muted" style="font-size:0.75rem">正在建立数据基线<span class="dots"></span></div>
      </div>`;
    return;
  }

  // 空状态
  if (!top10 || top10.length === 0) {
    const sc = stats.state_counts || {};
    const anomalyCount = sc.ANOMALY || 0;
    view.innerHTML = `
      <div class="empty-state">
        <div class="icon">📡</div>
        <div class="title">暂无确认启动机会</div>
        <div class="desc">
          系统正在监控 ${stats.universe_size || 0} 个交易对。
          ${anomalyCount > 0 ? `当前有 ${anomalyCount} 个异动候选，尚未通过启动确认。` : '当前无异动候选。'}
          <br>继续等待资金信号。
        </div>
      </div>`;
    return;
  }

  let html = '';

  // 系统结论
  const conclusion = summary.conclusion || '当前无确认启动机会，建议等待。';
  const hasOpportunity = top10.some(s => s.state === 'START_CONFIRMED' || s.state === 'CONTINUATION');
  html += `<div class="conclusion ${hasOpportunity ? '' : 'empty'}">${escapeHtml(conclusion)}</div>`;

  // 统计栏
  html += renderSummaryBar(stats);

  // Top10 卡片
  html += '<div class="card-grid">';

  // Hero Card (Top1)
  if (top10.length > 0) {
    html += renderHeroCard(top10[0]);
  }

  // 标准卡片 (Top2-10)
  for (let i = 1; i < top10.length; i++) {
    html += renderCard(top10[i], i + 1);
  }

  html += '</div>';

  // Preview Panel
  if (State.previewSymbol) {
    html += `<div class="preview-panel show" id="preview-panel"></div>`;
  }

  view.innerHTML = html;

  // 异步加载 preview
  if (State.previewSymbol) {
    loadPreview(State.previewSymbol);
  }
}

function renderSummaryBar(stats) {
  const sc = stats.state_counts || {};
  const dataStatus = stats.data_status || '未知';
  const statusClass = dataStatus === '数据正常' ? 'live' : (dataStatus === '数据降级' ? 'degraded' : 'error');

  let html = `<div class="summary-bar">`;
  html += `<div class="data-pill ${statusClass}"><span class="dot"></span>${escapeHtml(dataStatus)}</div>`;
  html += `<div class="chip"><span class="label">Universe</span><span class="value accent">${stats.universe_size || 0}</span></div>`;
  html += `<div class="chip"><span class="label">候选</span><span class="value">${stats.candidate_count || 0}</span></div>`;
  html += `<div class="chip"><span class="label">确认启动</span><span class="value long">${sc.START_CONFIRMED || 0}</span></div>`;
  html += `<div class="chip"><span class="label">延续</span><span class="value accent">${sc.CONTINUATION || 0}</span></div>`;
  html += `<div class="chip"><span class="label">疑似启动</span><span class="value warning">${sc.SUSPECTED_START || 0}</span></div>`;
  html += `<div class="chip"><span class="label">异动</span><span class="value warning">${sc.ANOMALY || 0}</span></div>`;
  html += `<div class="chip"><span class="label">衰竭</span><span class="value warning">${sc.EXHAUSTION || 0}</span></div>`;
  html += `<div class="chip"><span class="label">撤离</span><span class="value short">${sc.WITHDRAWAL || 0}</span></div>`;
  html += `</div>`;
  return html;
}

function renderHeroCard(s) {
  const dir = s.direction || '';
  const dirClass = dir === 'LONG' ? 'long' : (dir === 'SHORT' ? 'short' : '');
  const oppScore = s.opportunity_score;
  const confPct = s.confidence_pct;

  let scoreBars = '';
  const labels = {
    capital_inflow: '资金输入', startup_quality: '启动质量', trend: '趋势',
    immediate_stamina: '即时续航', sustained_startup: '持续启动',
  };
  if (s.score_breakdown && s.score_breakdown.subscores) {
    for (const [key, label] of Object.entries(labels)) {
      const ss = s.score_breakdown.subscores[key];
      if (ss && ss.available) {
        const color = scoreColor(ss.score, false);
        scoreBars += `<div class="score-bar"><span class="name">${label}</span><div class="track"><div class="fill ${color}" style="width:${ss.score}%"></div></div><span class="num">${fmt(ss.score, 0)}</span></div>`;
      }
    }
  }

  // 风险分
  const riskLabels = { top_risk: '顶部风险', crowding_risk: '拥挤风险', withdrawal_risk: '撤离风险', chase_risk: '追涨风险' };
  let riskBars = '';
  if (s.score_breakdown && s.score_breakdown.subscores) {
    for (const [key, label] of Object.entries(riskLabels)) {
      const ss = s.score_breakdown.subscores[key];
      if (ss && ss.available) {
        const color = scoreColor(ss.score, true);
        riskBars += `<div class="score-bar"><span class="name">${label}</span><div class="track"><div class="fill ${color}" style="width:${ss.score}%"></div></div><span class="num">${fmt(ss.score, 0)}</span></div>`;
      }
    }
  }

  return `
    <div class="hero-card ${dirClass}" onclick="selectSymbol('${s.symbol}')">
      <div class="hero-header">
        <div>
          <span class="hero-rank">#1</span>
          <span class="hero-symbol">${s.symbol}</span>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <span class="badge badge-state-${s.state}">${s.state_display || s.state_label || s.state}</span>
          ${dir ? `<span class="badge badge-${dir.toLowerCase()}">${s.direction_label || (dir === 'LONG' ? '做多' : dir === 'SHORT' ? '做空' : dir)}</span>` : ''}
        </div>
      </div>
      <div class="hero-body">
        <div class="hero-scores">
          <div class="score-row">
            <span class="label">机会分</span>
            <span class="value big text-accent">${oppScore != null ? fmt(oppScore, 1) : '-'}</span>
          </div>
          <div class="score-row">
            <span class="label">置信度</span>
            <span class="value">${confPct != null ? fmt(confPct, 0) + '%' : '-'}</span>
          </div>
          <div style="margin-top:8px">${scoreBars}</div>
        </div>
        <div>
          <div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:4px">风险</div>
          ${riskBars}
          <div class="card-summary">${escapeHtml(s.summary || '')}</div>
        </div>
      </div>
    </div>`;
}

function renderCard(s, rank) {
  const dir = s.direction || '';
  const dirClass = dir === 'LONG' ? 'long' : (dir === 'SHORT' ? 'short' : '');
  const oppScore = s.opportunity_score;
  const confPct = s.confidence_pct;

  let scoreBars = '';
  const labels = {
    capital_inflow: '资金输入', startup_quality: '启动质量',
    immediate_stamina: '续航', sustained_startup: '持续',
  };
  if (s.score_breakdown && s.score_breakdown.subscores) {
    for (const [key, label] of Object.entries(labels)) {
      const ss = s.score_breakdown.subscores[key];
      if (ss && ss.available) {
        const color = scoreColor(ss.score, false);
        scoreBars += `<div class="score-bar"><span class="name">${label}</span><div class="track"><div class="fill ${color}" style="width:${ss.score}%"></div></div><span class="num">${fmt(ss.score, 0)}</span></div>`;
      }
    }
  }

  return `
    <div class="card ${dirClass}" onclick="selectSymbol('${s.symbol}')">
      <div class="card-header">
        <div style="display:flex;align-items:center;gap:8px">
          <span class="card-rank">#${rank}</span>
          <span class="card-symbol">${s.symbol}</span>
        </div>
        <div style="display:flex;gap:4px">
          <span class="badge badge-state-${s.state}">${s.state_display || s.state_label || s.state}</span>
        </div>
      </div>
      <div class="card-price">
        ${fmtPct(s.price_change_24h)} <span class="${pctColor(s.price_change_24h)}" style="font-size:0.7rem">24h</span>
      </div>
      <div style="display:flex;gap:12px;margin-bottom:4px">
        <span style="font-size:0.72rem;color:var(--text-muted)">机会分 <span class="text-accent font-mono" style="font-weight:700">${oppScore != null ? fmt(oppScore, 1) : '-'}</span></span>
        <span style="font-size:0.72rem;color:var(--text-muted)">置信度 <span class="font-mono" style="font-weight:600">${confPct != null ? fmt(confPct, 0) + '%' : '-'}</span></span>
      </div>
      ${scoreBars}
      <div class="card-summary">${escapeHtml(s.summary || '')}</div>
    </div>`;
}

function selectSymbol(symbol) {
  // 在首页：显示 preview；在其他页面：跳转详情
  if (State.currentPage === 'home') {
    State.previewSymbol = symbol;
    loadPreview(symbol);
  } else {
    navigate('/symbol/' + symbol);
  }
}

async function loadPreview(symbol) {
  const panel = document.getElementById('preview-panel');
  if (!panel) return;
  panel.innerHTML = '<div class="loading-text">加载中<span class="dots"></span></div>';

  const detail = await API.getSymbolDetail(symbol);
  if (!detail || detail.error) {
    panel.innerHTML = '<div class="loading-text">数据不足</div>';
    return;
  }

  panel.innerHTML = renderDetailContent(detail, true);
}

// ═══════════════════════════════════════
// 全市场页面
// ═══════════════════════════════════════
function renderMarket(view) {
  let radar = State.radarData || [];

  // 搜索过滤
  if (State.searchQuery) {
    const q = State.searchQuery.toUpperCase();
    radar = radar.filter(s => s.symbol.includes(q));
  }

  // 状态过滤
  if (State.filter !== 'all') {
    radar = radar.filter(s => s.state === State.filter);
  }

  // 排序
  const sortKey = State.sortKey;
  const dir = State.sortDir === 'asc' ? 1 : -1;
  radar.sort((a, b) => {
    let va, vb;
    switch (sortKey) {
      case 'opportunity': va = a.opportunity_score || 0; vb = b.opportunity_score || 0; break;
      case 'confidence': va = a.confidence || 0; vb = b.confidence || 0; break;
      case 'capital': va = a.score_breakdown?.subscores?.capital_inflow?.score || 0; vb = b.score_breakdown?.subscores?.capital_inflow?.score || 0; break;
      case 'stamina': va = a.score_breakdown?.subscores?.immediate_stamina?.score || 0; vb = b.score_breakdown?.subscores?.immediate_stamina?.score || 0; break;
      case 'price_change': va = a.price_change_24h || 0; vb = b.price_change_24h || 0; break;
      case 'updated': va = a.last_update_ms || 0; vb = b.last_update_ms || 0; break;
      default: va = (a.opportunity_score || 0) * (a.confidence || 0); vb = (b.opportunity_score || 0) * (b.confidence || 0);
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
    { key: 'confidence', label: '置信度' },
    { key: 'capital', label: '资金输入' },
    { key: 'stamina', label: '续航' },
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
  filterHtml += `<div class="filter-btn" onclick="toggleSortDir()">${State.sortDir === 'asc' ? '↑' : '↓'}</div>`;
  filterHtml += '</div>';

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
        <thead>
          <tr>
            <th style="width:40px">#</th>
            <th onclick="setSort('ranking')">币种</th>
            <th onclick="setSort('opportunity')" class="${State.sortKey === 'opportunity' ? 'sorted' : ''} ${State.sortDir}">机会</th>
            <th onclick="setSort('confidence')" class="${State.sortKey === 'confidence' ? 'sorted' : ''} ${State.sortDir}">置信度</th>
            <th>状态</th>
            <th>方向</th>
            <th onclick="setSort('capital')">资金</th>
            <th>续航</th>
            <th onclick="setSort('price_change')">24h</th>
            <th>更新</th>
          </tr>
        </thead>
        <tbody>`;

  for (let i = 0; i < radar.length; i++) {
    const s = radar[i];
    const dir = s.direction || '';
    const opp = s.opportunity_score;
    const conf = s.confidence_pct;
    const capScore = s.score_breakdown?.subscores?.capital_inflow?.score;
    const stamScore = s.score_breakdown?.subscores?.immediate_stamina?.score;

    tableHtml += `
      <tr class="clickable" onclick="navigate('/symbol/${s.symbol}')">
        <td class="text-muted">${i + 1}</td>
        <td><span style="font-weight:600;color:var(--accent)">${s.symbol}</span></td>
        <td class="num">${opp != null ? fmt(opp, 1) : '-'}</td>
        <td class="num">${conf != null ? fmt(conf, 0) + '%' : '-'}</td>
        <td><span class="badge badge-state-${s.state}">${s.state_label || s.state}</span></td>
        <td>${dir ? `<span class="badge badge-${dir.toLowerCase()}">${s.direction_label || (dir === 'LONG' ? '做多' : dir === 'SHORT' ? '做空' : dir)}</span>` : '-'}</td>
        <td class="num">${capScore != null ? fmt(capScore, 0) : '-'}</td>
        <td class="num">${stamScore != null ? fmt(stamScore, 0) : '-'}</td>
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
// 详情页
// ═══════════════════════════════════════
async function renderDetail(view) {
  const symbol = State.detailSymbol;
  if (!symbol) { navigate('/'); return; }

  view.innerHTML = '<div class="loading-text">加载详情<span class="dots"></span></div>';

  const detail = await API.getSymbolDetail(symbol);
  if (!detail || detail.error || detail.state === 'NO_DATA') {
    view.innerHTML = `
      <div class="empty-state">
        <div class="icon">📭</div>
        <div class="title">${symbol} 暂无数据</div>
        <div class="desc">该交易对可能尚未进入深度分析，或数据正在预热中。</div>
        <div class="mt-md"><button class="filter-btn" onclick="navigate('/')">返回首页</button></div>
      </div>`;
    return;
  }

  view.innerHTML = renderDetailContent(detail, false);
  // 绑定模式切换
  window.toggleDetailMode = function() {
    State.detailMode = State.detailMode === 'normal' ? 'pro' : 'normal';
    renderPage();
  };
  // 绑定展开
  document.querySelectorAll('.expand-btn').forEach(btn => {
    btn.onclick = function() {
      const target = document.getElementById(this.dataset.target);
      if (target) target.classList.toggle('show');
      this.textContent = target.classList.contains('show') ? '收起' : '展开';
    };
  });
}

function renderDetailContent(d, isPreview) {
  const dir = d.direction || '';
  const opp = d.opportunity_score;
  const conf = d.confidence_pct;
  const mode = State.detailMode;

  let html = '';

  // 顶部结论
  html += `<div class="detail-header">`;
  if (isPreview) {
    html += `<div style="display:flex;align-items:center;gap:12px;width:100%;justify-content:space-between">
      <div class="detail-symbol">${d.symbol}</div>
      <button class="filter-btn" onclick="navigate('/symbol/${d.symbol}')">查看完整详情 →</button>
    </div>`;
  } else {
    html += `<div class="detail-symbol">${d.symbol}</div>`;
    html += `<div style="display:flex;gap:6px">
      <span class="badge badge-state-${d.state}">${d.state_display || d.state_label || d.state}</span>
      ${dir ? `<span class="badge badge-${dir.toLowerCase()}">${d.direction_label || (dir === 'LONG' ? '做多' : dir === 'SHORT' ? '做空' : dir)}</span>` : ''}
    </div>`;
  }
  html += `</div>`;

  // 分数 + 置信度 + 数据状态
  html += `<div class="detail-conclusion">
    <div style="display:flex;gap:24px;margin-bottom:8px">
      <div><span class="text-muted" style="font-size:0.75rem">机会分</span> <span class="text-accent font-mono" style="font-size:1.3rem;font-weight:700">${opp != null ? fmt(opp, 1) : '-'}</span></div>
      <div><span class="text-muted" style="font-size:0.75rem">置信度</span> <span class="font-mono" style="font-size:1.3rem;font-weight:700">${conf != null ? fmt(conf, 0) + '%' : '-'}</span></div>
      <div><span class="text-muted" style="font-size:0.75rem">数据状态</span> <span style="font-weight:600">${d.confidence_state_label || d.confidence_state || '-'}</span></div>
    </div>
    <div>${escapeHtml(d.summary || '')}</div>
  </div>`;

  // 模式切换（仅完整详情）
  if (!isPreview) {
    html += `<div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:center">
      <div class="mode-toggle">
        <button class="mode-btn ${mode === 'normal' ? 'active' : ''}" onclick="toggleDetailMode()">普通模式</button>
        <button class="mode-btn ${mode === 'pro' ? 'active' : ''}" onclick="toggleDetailMode()">专业模式</button>
      </div>
    </div>`;
  }

  // ── 评分模块 ──
  if (d.score_breakdown && d.score_breakdown.subscores) {
    const labels = d.subscore_labels || {};
    html += `<div class="module">
      <div class="module-title">评分详情</div>`;

    // 基础分
    for (const [key, ss] of Object.entries(d.score_breakdown.subscores)) {
      if (ss.is_risk) continue;
      const label = labels[key] || key;
      const color = scoreColor(ss.score, false);
      html += `<div class="score-bar">
        <span class="name" style="width:80px">${label}</span>
        <div class="track"><div class="fill ${color}" style="width:${ss.score}%"></div></div>
        <span class="num">${fmt(ss.score, 1)}</span>
      </div>`;
      // 专业模式展开组件
      if (mode === 'pro' && ss.components) {
        html += `<div class="pro-data show" style="padding-left:80px">`;
        for (const c of ss.components) {
          html += `<div class="pro-row"><span>${c.name}</span><span class="v">${c.value != null ? fmt(c.value, 4) : '-'} → ${fmt(c.contribution, 1)}</span></div>`;
        }
        html += `</div>`;
      }
    }

    // 风险分
    html += `<div style="font-size:0.7rem;color:var(--text-muted);margin:8px 0 4px">风险</div>`;
    for (const [key, ss] of Object.entries(d.score_breakdown.subscores)) {
      if (!ss.is_risk) continue;
      const label = labels[key] || key;
      const color = scoreColor(ss.score, true);
      html += `<div class="score-bar">
        <span class="name" style="width:80px">${label}</span>
        <div class="track"><div class="fill ${color}" style="width:${ss.score}%"></div></div>
        <span class="num">${fmt(ss.score, 1)}</span>
      </div>`;
    }

    html += `</div>`;
  }

  // ── 资金行为模块 ──
  if (d.capital_flow) {
    html += `<div class="module">
      <div class="module-title">资金行为
        <span class="expand-btn" data-target="capital-pro">展开</span>
      </div>`;
    for (const [label, value] of Object.entries(d.capital_flow)) {
      const valClass = value.includes('强') || value.includes('明显') || value.includes('良好') ? 'good' :
                       value.includes('弱') || value.includes('减少') || value.includes('撤离') ? 'bad' :
                       value.includes('偏高') || value.includes('高') ? 'warn' : '';
      html += `<div class="user-item"><span class="label">${label}</span><span class="value ${valClass}">${escapeHtml(value)}</span></div>`;
    }
    // 专业数据
    html += `<div class="pro-data" id="capital-pro">`;
    const fv = d.features || {};
    for (const key of ['taker_buy_volume', 'taker_sell_volume', 'signed_delta', 'cvd', 'CVD_slope', 'cvd_slope_z', 'oi_contracts', 'oi_change_30s', 'oi_change_1m', 'oi_change_5m', 'funding', 'premium']) {
      const v = fv[key];
      if (v != null) html += `<div class="pro-row"><span>${key}</span><span class="v">${fmt(v, 4)}</span></div>`;
    }
    html += `</div></div>`;
  }

  // ── 量价模块 ──
  if (d.volume_price) {
    html += `<div class="module">
      <div class="module-title">量价分析
        <span class="expand-btn" data-target="vp-pro">展开</span>
      </div>`;
    for (const [label, value] of Object.entries(d.volume_price)) {
      const valClass = value.includes('放大') || value.includes('健康') || value.includes('良好') || value.includes('确认') ? 'good' :
                       value.includes('偏低') || value.includes('较差') || value.includes('未确认') ? 'bad' : '';
      html += `<div class="user-item"><span class="label">${label}</span><span class="value ${valClass}">${escapeHtml(value)}</span></div>`;
    }
    html += `<div class="pro-data" id="vp-pro">`;
    const fv = d.features || {};
    for (const key of ['volume_z', 'trade_count_z', 'price_acceleration', 'price_efficiency', 'retrace_ratio', 'acceptance', 'directional_efficiency', 'flow_impact']) {
      const v = fv[key];
      if (v != null) html += `<div class="pro-row"><span>${key}</span><span class="v">${fmt(v, 4)}</span></div>`;
    }
    html += `</div></div>`;
  }

  // ── 假启动检查 ──
  if (d.false_start_check && d.false_start_check.length > 0) {
    html += `<div class="module">
      <div class="module-title">假启动检查</div>`;
    for (const check of d.false_start_check) {
      html += `<div class="check-item"><span class="icon">${check.display.substring(0, 2)}</span><span>${check.display.substring(2)}</span></div>`;
    }
    html += `</div>`;
  }

  // ── 证据链（专业模式）──
  if (mode === 'pro' && d.evidence && d.evidence.length > 0) {
    html += `<div class="module">
      <div class="module-title">证据链 (${d.evidence.length})</div>`;
    for (const e of d.evidence) {
      html += `<div class="pro-row"><span>[${e.family}] ${e.type}</span><span class="v">${fmt(e.value, 4)} ${e.passed ? '✓' : '✗'} ${e.threshold != null ? '(阈值' + e.threshold + ')' : ''}</span></div>`;
    }
    html += `</div>`;
  }

  // ── 状态时间轴 ──
  if (d.timeline && d.timeline.length > 0) {
    html += `<div class="module">
      <div class="module-title">状态时间轴</div>
      <div class="timeline">`;
    const tl = [...d.timeline].reverse();
    for (const t of tl) {
      html += `<div class="timeline-item"><span class="time">${ts(t.time)}</span><span class="label">${t.state}</span></div>`;
    }
    html += `</div></div>`;
  }

  return html;
}

// ═══════════════════════════════════════
// 信号中心
// ═══════════════════════════════════════
function renderSignals(view) {
  const signals = State.signalsData || [];

  if (signals.length === 0) {
    view.innerHTML = `
      <div class="empty-state">
        <div class="icon">🔕</div>
        <div class="title">暂无信号</div>
        <div class="desc">系统尚未检测到状态变化。信号将在检测到启动、延续、衰竭或撤离时出现。</div>
      </div>`;
    return;
  }

  // 按时间倒序
  const sorted = [...signals].reverse();

  let html = '<div class="card-grid">';
  for (const s of sorted.slice(0, 50)) {
    const dir = s.direction || '';
    const dirLabel = s.direction_label || (dir === 'LONG' ? '做多' : dir === 'SHORT' ? '做空' : '');
    html += `
      <div class="card" onclick="navigate('/symbol/${s.symbol}')">
        <div class="card-header">
          <span class="card-symbol">${s.symbol}</span>
          <span class="badge badge-state-${s.state}">${s.state_display || s.state_label || s.state}</span>
        </div>
        <div class="card-price" style="margin-bottom:4px">
          ${dir ? `<span class="badge badge-${dir.toLowerCase()}">${dirLabel}</span>` : ''}
        </div>
        <div class="card-summary">
          <span class="text-muted">${ts(s.asof)}</span>
          ${s.evidence_count ? ` · 证据 ${s.evidence_count}` : ''}
          ${s.veto_count ? ` · 否决 ${s.veto_count}` : ''}
        </div>
      </div>`;
  }
  html += '</div>';

  view.innerHTML = html;
}

// ═══════════════════════════════════════
// 数据健康
// ═══════════════════════════════════════
function renderHealth(view) {
  const health = State.healthData || [];

  if (!health || health.length === 0) {
    view.innerHTML = `
      <div class="empty-state">
        <div class="icon">⚙️</div>
        <div class="title">数据健康信息加载中</div>
        <div class="desc">正在采集各数据流的状态信息<span class="dots"></span></div>
      </div>`;
    return;
  }

  const streamLabels = {
    aggTrade: 'AggTrade', kline: 'Kline', oi_poller: 'OI', funding_premium: 'Funding',
  };

  let html = `
    <div class="table-wrap">
      <table class="health-table">
        <thead>
          <tr>
            <th>交易对</th>
            <th>AggTrade</th>
            <th>Kline</th>
            <th>OI</th>
            <th>Funding</th>
            <th>置信度</th>
          </tr>
        </thead>
        <tbody>`;

  for (const row of health) {
    html += `<tr><td><span style="font-weight:600;color:var(--accent)">${row.symbol}</span></td>`;
    for (const prefix of ['aggTrade', 'kline', 'oi_poller', 'funding_premium']) {
      const stream = row[prefix];
      if (stream) {
        const status = stream.status || 'FAIL';
        const statusLabel = stream.status_label || status;
        html += `<td><span class="status-cell health-${status}"><span class="dot"></span>${escapeHtml(statusLabel)}</span></td>`;
      } else {
        html += `<td>-</td>`;
      }
    }
    const conf = row.confidence_state || 'UNKNOWN';
    const confLabel = row.confidence_state_label || conf;
    const confClass = conf === 'CONFIDENT' ? 'text-long' : conf === 'DEGRADED' ? 'text-short' : 'text-short';
    html += `<td class="${confClass}" style="font-weight:600">${escapeHtml(confLabel)}</td>`;
    html += `</tr>`;
  }

  html += '</tbody></table></div>';
  view.innerHTML = html;
}

// ═══════════════════════════════════════
// 回放验证
// ═══════════════════════════════════════
function renderReplay(view) {
  // Replay API 尚未实现，显示占位
  view.innerHTML = `
    <div class="empty-state">
      <div class="icon">📊</div>
      <div class="title">回放验证</div>
      <div class="desc">
        回放验证页面用于验证评分体系有效性，包括：<br>
        · 过去 7 天确认启动次数<br>
        · 假启动拦截次数<br>
        · 1m / 5m / 15m / 1h 后方向表现<br>
        · 机会分分桶表现<br><br>
        <span class="text-muted">该功能需要积累足够的运行数据后启用。</span>
      </div>
    </div>`;
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
    <div class="search-result" onclick="navigate('/symbol/${s.symbol}');clearSearch()">
      <span class="sym">${s.symbol}</span>
      <span class="meta">${s.state_label || s.state} · ${s.opportunity_score != null ? fmt(s.opportunity_score, 0) + '分' : '-'}</span>
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
// 数据轮询
// ═══════════════════════════════════════
let pollTimer = null;

async function pollData() {
  const tasks = [];

  // 首页和全市场需要 radar
  if (State.currentPage === 'home' || State.currentPage === 'market') {
    tasks.push(API.getRadar().then(d => { if (d) State.radarData = d; }));
  }

  // 首页需要 top10 + summary + stats
  if (State.currentPage === 'home') {
    tasks.push(API.getTop10().then(d => { if (d) State.top10Data = d; State.loading = false; }));
    tasks.push(API.getMarketSummary().then(d => { if (d) State.summaryData = d; }));
    tasks.push(API.getStats().then(d => { if (d) State.statsData = d; }));
  }

  // 全市场和首页需要 stats
  if (State.currentPage === 'market') {
    tasks.push(API.getStats().then(d => { if (d) State.statsData = d; }));
  }

  // 信号中心
  if (State.currentPage === 'signals') {
    tasks.push(API.getSignals().then(d => { if (d) State.signalsData = d; }));
  }

  // 数据健康
  if (State.currentPage === 'health') {
    tasks.push(API.getHealth().then(d => { if (d) State.healthData = d; }));
  }

  await Promise.all(tasks);

  // 只在非详情页自动刷新（详情页按需加载）
  if (State.currentPage !== 'detail') {
    renderPage();
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollData();
  pollTimer = setInterval(pollData, 3000);
}

// ═══════════════════════════════════════
// 初始化
// ═══════════════════════════════════════
function initApp() {
  // 导航点击
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => navigate(el.dataset.route));
  });

  // 搜索
  const searchInput = document.getElementById('search-input');
  if (searchInput) {
    searchInput.addEventListener('input', e => handleSearch(e.target.value));
    searchInput.addEventListener('focus', e => { if (e.target.value) handleSearch(e.target.value); });
  }

  // 点击外部关闭搜索
  document.addEventListener('click', e => {
    if (!e.target.closest('.search-box')) {
      const dd = document.getElementById('search-dropdown');
      if (dd) dd.classList.remove('show');
    }
  });

  // `/` 快捷键聚焦搜索
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

  // 路由
  window.addEventListener('hashchange', handleRoute);

  // 启动轮询
  startPolling();

  // 首次路由
  handleRoute();
}

// 等待 DOM 就绪
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
