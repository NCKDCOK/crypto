# UI 架构（V1.3 P3 重构）

| 版本 | v1.1 |
|------|------|
| 依据 | 《资金行为雷达_V1.3_状态监督_模拟验证_UI重构_更新计划》§12-§17, §33-§42, §51-§57, §69-§71 |
| 状态 | 已交付（冒烟 §66.8 通过），文档随 CRYPTO_MARKET_RADAR docs 目录维护 |

**产品原则（§72）：系统对一个机会从发现到结束负责到底。** UI 是对该闭环（发现 → 监督 → 推荐 → 验证 → 跟踪 → 退出 → 统计 → 校准）的可视化外壳，而非指标堆砌的 Debug Dashboard。

---

## 0. 页面清单（5 页，其余冗余页已合并/删除）

| 导航 | 路由 | 渲染函数 | 数据来源 |
|------|------|----------|----------|
| 首页 | `#/` | `renderHome` | `getHome` |
| 全市场 | `#/market` | `renderMarket` | `getMarket`（全市场）+ `getRadar`（慢轮询） |
| 监督台 | `#/supervision` | `renderSupervision` | `getSupervision`（Kanban 矩阵） |
| 模拟验证 | `#/simulations` | `renderSimulations` | `getSimulations`（队列/持仓/结果/统计） |
| 数据健康 | `#/health` | `renderHealth` | `getHealth`（覆盖率 + 明细） |

SPA 无框架：`static/index.html`（59 行，nav 5 项 + `#view` + `#side-drawer` + `#drawer-overlay`）+ `static/js/api.js`（15 个 API 方法）+ `static/js/app.js`（~1500 行，渲染/轮询/交互）+ `static/css/style.css`。

---

## 1. 轮询节奏（§12，P0 已配置化于 `configs/ranking.yaml`）

| 定时器 | 周期 | 首页动作 | 全市场动作 |
|--------|------|----------|------------|
| `priceTimer` | 4s | 价格轻量刷新 | 价格 |
| `dataTimer` | 10s | 主数据轮询 | 主数据轮询 |
| `slowTimer` | 30s | `getHome` + `getStats` | `getRadar`（全市场 30s 维度） |
| `topTimer` | 60s | 重拉 `getHome` | — |

分层轮询的目的：**首页不秒级重排**（§12/§66.8）。趋势箭头存 `State.scoreTrend[symbol] = {opp,sc,dc,subs,accum}`，diff > +1 显示 ↑、< -1 显示 ↓、否则 →。§54 顺序：先 `State.homeData = home → renderHomeIfChanged(home)`（Up 用上一轮趋势），再 `captureScoreTrend(home)`（存新基线）。

首页渲染节流：`homeMateriallyChanged(home)` 对比 `lastRendered.home`（confirmed 张数 / symbol / state / direction / 快照 opp±1 / 快照 sc±1 / watch / risk symbol 串），无实质变化不重排 DOM。

---

## 2. 首页（§15/§16/§69）

### 2.1 top-stats 六字段

市场背景（regime 中文 + detail）、数据健康（覆盖率 + 等级 + 健康配对）、Universe 数、重点观察数、确认机会数、风险中数。

### 2.2 Top Opportunities（§13）

- **≤ 10 张**（不强制凑满）；COOLDOWN / 无信号币不进入。
- 空态文案（§13 原文）：「当前暂无确认机会。系统正在重点观察 N 个候选。」
- 卡片字段（§16）：Symbol / 当前价 / 24h 涨跌 / 状态 / Setup / 方向 / 主周期 / 机会分 / 信号确认 / 数据可信 / 6+1 子评分 tiles（资金输入/启动质量/持续启动/即时续航/吸筹迹象/追涨安全 + 撤离风险 risk 色）/ 监督阶段 / 模拟 mini 徽标（§52）/ 计划摘要（`plan_reason`，或「等待 low~high 回踩重新确认」，SUSPECTED_START 显示「候选预案，尚未确认」）/ summary。
- 主值 = `decision_snapshot.decision` 冻结值（§55），实时值仅在 Drawer 双值区（§56）出现。

### 2.3 正在观察 / 风险中

watch（ANOMALY/SUSPECTED_START 等）与 risk（EXHAUSTION/WITHDRAWAL，显示 Pump/派发分）两栏，点击同开 Drawer。

---

## 3. Side Drawer（§17/§28-§34/§41-§42/§56，区块 A–I）

右侧滑出，`renderHomeCard`/`renderMarketCard`/`renderSupervisionCard` 点击进入。区块：

| 区块 | 内容 | 关键点 |
|------|------|--------|
| A 当前结论 | 状态/方向/Setup/主周期 + 快照时间（`frozen_at`） | 冻结值来源 |
| B 核心评分 | 推荐时 → 当前 双值 + 箭头（§56） | rec 来自 snapshot，cur 来自实时 |
| C 当前计划 | 参考关注区 / 结构失效位 / TP1-3 / R:R / 建议追 | 仅正式计划且 ACTIVE 显示正文 |
| D 生命周期 | 发现异动 → 疑似启动 → 确认启动 → 趋势延续 → 衰竭 → 撤离 6 节点 | |
| E 评分明细 | 非 risk / risk 两组 score-bar | |
| F 资金摘要 | RVOL / Taker B/S / Delta / CVD / OI 5m·15m·1h / Funding / Premium / Spot CVD / Perp CVD + 现货×合约 / 多空推动 | OI 用 `oi_change_pct_*` 优先，fallback `oi_change_*`（§17） |
| G 突破生命周期 | 突破时间/位/保持/回撤深度/首次回踩/二次确认/强确认/距局部高点 | 距局部高点 = `(price/local_high - 1)%` |
| H Evidence/Veto | 核心证据 n/n · 辅助证据 n/n · ✅/❌ Veto · 确认强度 | |
| I 模拟状态 | 队列状态 + OPEN 持仓（Entry/Current/PnL/MFE/MAE/Exit Trigger）或终态结果 | `renderDrawerSimulation`，stageOrder 取最前活跃 |

实现提示：Drawer 每次打开重新 `innerHTML` 渲染，内容锚定 snapshot 冻结值，不随轮询漂移（§66.8「Drawer 内容稳定」）。

---

## 4. 监督台（§5-§10/§41-§42/§70）

- 6 列 Kanban（KANBAN_COLUMNS：anomaly/watch/confirmed/continuation/risk/exit），每列头部计数。
- 卡片（§41）：symbol / 状态 badge / 方向 / Setup chip / 已观察时长 / 机会分 / 确认度 / **池级监督问题**（`supervision_question`，来自 PoolSpec，8 条中文）。
- 点击卡片 → 监督 Drawer：监督池/监督级别/进入本池/进入本状态/连续失败/上次动作/当前价格/派发风险/Pump风险/监督问题 + **§42 状态日志·Setup 时间线**（`transition_history` 过滤该 symbol，最新在前）。
- 池分组逻辑：`supervisor.by_pool()` 按 pool_name.value 分组，COOLDOWN 不入正式列。

---

## 5. 模拟验证（§33-§39/§71）

### 5.1 顶部 sim-counts

等待入场 / 运行中 / 已结束 三个计数 chip（等待入场 warning 色、运行中 accent 色）。

### 5.2 五个 Tab

| Tab | 内容 |
|-----|------|
| 等待入场 | 队列中 WATCHING/ENTRY_ZONE_REACHED/REVALIDATING/ARMED/SIMULATED_ENTRY 项：推荐时间/推荐价/参考关注区/当前价/距离关注区/机会分/确认度/当前状态 |
| 运行中 | OPEN 持仓：Entry/Current/PnL/MFE/MAE/TP1/Invalidation/当前状态/资金变化/撤离风险 |
| 已结束 | CLOSED 队列 + 结果合并：Entry/Exit/Result/Exit Reason/MFE/MAE/Duration/机会/确认 |
| 统计 | §37-§39 汇总：推荐次数/进入观察区/通过 Revalidation/模拟入场/TP1·TP2/失效/撤离退出 + 平均 MFE/MAE + 分桶（机会分/确认度/Setup/方向/时间框架/市场背景）+ Setup 转化率 |
| 历史回放 | 每单完整闭环：推荐快照 → 观察→入场 → 退出原因 → 动态结果 vs 固定 TP/Stop 对比 |

### 5.3 状态常量

- `SIM_ACTIVE_WAITING = [WATCHING, ENTRY_ZONE_REACHED, REVALIDATING, ARMED, SIMULATED_ENTRY]`（等待入场 Tab）
- `SIM_TERMINAL = [CLOSED, EXPIRED, CANCELLED, INVALIDATED, MISSED]`（已结束 Tab）
- 中文标签：WATCHING=等待回踩 / ENTRY_ZONE_REACHED=进入关注区 / REVALIDATING=二次验证中 / ARMED=等待入场 / SIMULATED_ENTRY=已模拟入场 / OPEN=模拟跟踪中 / CLOSED=已结束 / EXPIRED=已过期 / CANCELLED=已取消 / INVALIDATED=已失效 / MISSED=已错过。

---

## 6. 数据健康（§46）

- 覆盖率大条：`coverage_pct`（健康符号×流对数 / 总数），等级：正常/部分降级/严重异常。
- 明细表：交易对 × 流（AggTrade/Kline/OI/Funding）状态 + 置信度（`data_confidence` 等级）。
- 核心流中断（aggTrade 整体断线）→ 严重异常横幅，优先于覆盖率阈值。

---

## 7. 关键实现约束

- **PnL/MFE/MAE 是百分比数值（%），前端不再 ×100，直接 `fmtPct`**；`max_retrace`/`retest_depth` 是 0–1 比例才 ×100。
- **快照主值冻结**：首页卡片主值取 `decision_snapshot.decision`，实时值只在 Drawer B 区（§55/§56）。
- `primary_timeframe` 不写入 decision（列表外层字段），Drawer A 区主周期来自行级 `primary_timeframe`。
- 首页顶层键钉死 5 个（market_regime/health/confirmed_opportunities/watch_candidates/risk_candidates），行级加键安全。
- 非 LIVE 模式：confirmed 空 + watch 仍返回（空态文案兜底），正式快照/Push/模拟快照不产出（§47）。
- 测试夹具：`scripts/ui_seed_server.py`（端口 8051，`lifespan="off"` + 手动挂 runtime）供 Playwright 冒烟稳定复现 9 币种全状态矩阵。

---

## 8. 与后端 API 对应

| API | 用途 |
|-----|------|
| `GET /api/home` | 首页：六字段 + Top10 + watch/risk（含 decision_snapshot/simulation_status/live_subscores/primary_timeframe/supervision） |
| `GET /api/market` | 全市场矩阵 |
| `GET /api/radar` | 全市场慢轮询雷达 |
| `GET /api/supervision` | 监督台 Kanban（by_pool） |
| `GET /api/supervision/{symbol}` | 监督 Drawer 详情 + timeline |
| `GET /api/simulations` | 模拟验证队列/持仓/结果 |
| `GET /api/simulations/{id}` | 单条模拟（item+position+events+result） |
| `GET /api/statistics` | 统计五段 |
| `GET /api/health` | 数据健康覆盖率 + 明细 |
| `GET /api/stats` | 顶部统计（Universe/重点观察/确认机会/风险中） |
| `GET /api/prices` | 价格轻量轮询 |