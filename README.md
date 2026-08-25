# 资金行为雷达 — Crypto Market Radar

> 全市场扫描 Binance USDT-M 永续合约"钱是否正在异常进入/退出"，先过滤假启动，再提醒用户关注真正有持续性的资金行为。
>
> **只读公开市场数据，只做分析提醒，不自动交易。**

---

## 这是什么

一个资金行为驱动的实时行情分析与启动雷达系统。核心研究对象不是 RSI/MACD，而是 **Price × Volume × Trade Flow × Open Interest** 的联动：识别资金异动、启动、延续、衰竭与撤离，输出可解释评分、Top 排行、用户化提醒，并对每个正式机会从发现到结束负责到底（推荐快照 → 模拟验证 → 持仓跟踪 → 结果统计）。

系统核心流程：

```
资金异动 → 疑似启动 → 假启动过滤 → 确认启动 → 趋势延续 → 衰竭 → 撤离
```

| | |
|---|---|
| **核心原则** | 先证明"数据可信"和"资金行为可解释"，再谈评分与解读 |
| **Evidence-first** | 任何分数必须可展开 → 原始 Evidence → 原始数据 |
| **可解释评分** | 11 个子评分 + 机会分 + 独立置信度，权重全部配置化 |
| **Fail Closed** | 数据不新鲜/不完整时输出 UNKNOWN / DATA_STALE，不继续造信号 |
| **机会闭环** | 发现 → 监督 → 推荐 → 验证 → 跟踪 → 退出 → 统计 → 校准 |
| **不做什么** | 自动下单、API Key、账户、仓位、杠杆、订单管理 |

---

## 项目状态

🟢 **V1.3 已落地 — 状态监督 × 模拟验证 × UI 重构**（含 V1.1 / V1.2 全部能力）

### V1.3 新增（最新）

| 模块 | 说明 |
|---|---|
| **状态监督（P1）** | 8 池监督体系（异动观察/等待确认/确认机会/趋势跟踪/风险/撤离/冷却/沉睡），每池独立监督规则 + 池级监督问题，状态迁移带滞回（单次评分下降不降级，连续失去证据才降级，Veto 立即失效），生命周期自发现到撤离全程可追踪 |
| **模拟验证（P2）** | 正式推荐自动生成不可变快照 → 自动加入模拟队列（12 状态）→ Entry Zone 二次验证 → 模拟入场/持仓跟踪（MFE/MAE/TP1-3/Stop/Withdrawal Exit）→ 动态退出 vs 固定计划双轨记录 → 统计（推荐/入场/TP1率/失效率/平均MFE/MAE/Setup 分组） |
| **UI 重构（P3）** | 5 页 SPA：首页 / 全市场 / 监督台 / 模拟验证 / 数据健康；首页不再秒级重排、Top10 不强制凑满、COOLDOWN 不进榜；详情合并为侧滑 Drawer（A–I 九区块） |
| **数据健康覆盖率（P0）** | 覆盖率口径（≥90% 正常 / 70~90% 部分降级 / <70% 异常），核心流断线优先判严重异常；单币 OI 延迟不再误报 |
| **排名阈值与节奏（P0）** | Top10 排除 COOLDOWN、不强制凑满；刷新节奏与阈值全部配置化（`configs/ranking.yaml`） |
| **Trade Plan 合法门控（P0）** | 计划只在合法状态出现；SUSPECTED_START 仅候选预案；冻结版本化（V1/V2）不漂移 |

### V1.2 新增

| 模块 | 说明 |
|---|---|
| **本地持久化 + 停机恢复** | SQLite 持久化（K线/OI/Funding/信号/TradePlan）+ `RecoveryManager` 三档恢复（<5m 快速 / 5m~1h 补历史 / >1h 重建）+ `SystemMode`（RECOVERY→WARMUP→LIVE）门控 |
| **市场背景** | 7 状态 Market Regime Engine（山寨强/弱、BTC 主导、震荡、去杠杆、恐慌、中性） |
| **结构位置** | Structure Engine（Swing/HH-HL/S/R/突破位/回踩区/VWAP/ATR）+ Volume Profile（POC/VAH-VAL/HVN-LVN） |
| **资金生命周期** | Accumulation/Absorption、Dormant Revival、Distribution、Impulse Asymmetry、Spot×Perp 一致性 |
| **置信度语义修复** | `data_confidence`（数据可信）与 `signal_confirmation`（信号确认）拆分，保留 `ConfidenceState` fail-closed 门 |
| **Trade Plan Engine** | Entry 来自结构（非 AI 自由生成）/ Invalidation / TP1-3 / R:R / chase_status；START_CONFIRMED 冻结快照不漂移 |
| **Push Engine** | 状态迁移推送（SUSPECTED→CONFIRMED / CONFIRMED→CONTINUATION / …），RECOVERY 期不发 |

### V1.1 新增

| 模块 | 说明 |
|---|---|
| **Runtime 修复 (P0)** | Stage1 增量异动、多周期 Kline、候选防抖、数据状态翻译、统一 Dashboard |
| **评分引擎** | 11 个子评分（7 基础 + 4 风险），OpportunityScore = 加权基础分 - 风险扣分 |
| **置信度引擎** | 独立于机会分，受数据健康/证据完整性/多窗口一致性影响 |
| **翻译层** | PresentationTranslator：内部术语 → 用户中文（状态/方向/资金行为/量价/假启动） |
| **Top10 排名** | RankingScore = OpportunityScore × ConfidenceFactor，排除 UNKNOWN/stale |

### 已 Live 的数据

| 数据源 | 状态 | 说明 |
|---|---|---|
| aggTrade (WS) | ✅ LIVE | 实时成交、aggressor side、CVD、taker delta、trade_id 去重 |
| Kline 1m/5m/15m/1h (WS) | ✅ LIVE | 多周期订阅 |
| Open Interest (REST) | ✅ LIVE | 基础资产数量、Δ30s/1m/5m、velocity、accel；变化统一 1h 口径、pct 展示 |
| Funding/Premium (REST) | ✅ LIVE | 拥挤度上下文、soft veto |
| 24h ticker (REST) | ✅ LIVE | 动态 universe 发现 + Stage1 短时增量扫描 |
| Spot aggTrade | ✅ LIVE | 现货成交/volume/CVD/taker/delta（V1.2），spot×perp 一致性 |

### 当前 Universe

动态：Binance USDT-M 永续 ACTIVE/TRADING，按 24h quote volume 排序 top-N（默认 100），支持 blacklist/whitelist/liquidity floor/max_symbols。
Stage2 深度分析候选 ≤ `max_deep_symbols`（默认 40，配 `configs/hysteresis.yaml`）。

---

## 如何启动

```bash
# 安装依赖（首次）
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate      # Linux/Mac
pip install -e .

# 启动
uvicorn src.main:app --host 127.0.0.1 --port 8050

# 浏览器访问
# http://127.0.0.1:8050/
```

> 受限网络环境在 `configs/app.yaml` 设置 `proxy: "http://127.0.0.1:7890"`

---

## 如何测试

```bash
pytest -q                                          # 全量离线测试（728 passed）
python scripts/live_smoke_test.py --duration 600   # 10 分钟 live 冒烟
python scripts/ui_seed_server.py 8051              # UI 冒烟种子服务器（Playwright 目标，端口 8051）
```

---

## UI 页面

| 页面 | 路由 | 功能 |
|---|---|---|
| **首页** | `#/` | top-stats 六字段（市场背景/数据健康/Universe/重点观察/确认机会/风险中）+ Top Opportunities（≤10，不凑满）+ 正在观察/风险中 |
| **全市场** | `#/market` | 搜索 + 状态筛选 + 多维排序 + 全量表格 |
| **监督台** | `#/supervision` | 6 列 Kanban（异动观察/等待确认/确认机会/趋势跟踪/风险/撤离），每币独立监督问题，点击卡片看监督详情 + 状态日志时间线 |
| **模拟验证** | `#/simulations` | 5 Tab：等待入场 / 运行中 / 已结束 / 统计（推荐次数/入场/TP1率/平均MFE/MAE/Setup 分组）/ 历史回放（动态退出 vs 固定 TP/Stop） |
| **数据健康** | `#/health` | 覆盖率大条 + 交易对×流明细 + 核心流中断横幅 |

任意卡片点击 → **侧滑 Drawer（A–I 九区块）**：当前结论 / 核心评分双值（推荐时→当前）/ 当前计划（关注区·失效位·TP1-3·R:R）/ 生命周期 / 评分明细 / 资金摘要（OI 5m·15m·1h 等 11 项）/ 突破生命周期 / Evidence-Veto / 模拟状态。

轮询节奏（配置化）：价格 4s / 主数据 10s / 慢轮询 30s / Top 重拉 60s；首页有实质变化才重排 DOM（不秒级重排）。

UI 风格：**科技感 + Apple 式克制丝滑** — 深色背景、冷青主色、大留白、柔和圆角、150-300ms 动效、响应式（768/1200 断点）。

---

## 评分体系

### 子评分（11 个，全部配置化权重 `configs/scoring.yaml`）

| 子评分 | 类型 | 回答 |
|---|---|---|
| 资金输入 | 基础 | 新增方向资金是否真的进入？ |
| 启动质量 | 基础 | 这次异动是不是像真正启动？ |
| 趋势 | 基础 | 当前方向是否稳定？ |
| 即时续航 | 基础 | 最近几十秒到几分钟，资金还在不在？ |
| 持续启动 | 基础 | 启动是不是只有一波，还是有持续性？ |
| 异动强度 | 基础 | 当前变化相对历史是否异常？ |
| 追涨安全 | 基础 | 现在是不是已经太晚？ |
| 顶部风险 | 风险 | 是否出现衰竭迹象？ |
| 拥挤风险 | 风险 | Funding/Premium 是否过热？ |
| 撤离风险 | 风险 | OI 衰减 / Delta 反转 / CVD 反转？ |
| 追涨风险 | 风险 | 延伸幅度 / 回撤风险？ |

### 公式

```
OpportunityScore = 加权基础分 - 风险扣分（risk_penalty_scale 缩放）
ConfidenceFactor = base - stale_penalty - degraded_penalty - missing_source_penalty - low_evidence_penalty
RankingScore    = OpportunityScore × ConfidenceFactor
```

- 机会分回答：这个机会本身好不好？
- 置信度回答：我们对这个判断有多大把握？
- 排名分回答：首页 Top10 该排谁？

---

## 项目结构

```
src/
├── collectors/          # 数据采集器（aggTrade/Kline/OI/Funding/Spot WS+REST）
├── features/            # 特征引擎（多窗口/基线/z-score/CVD/效率）
├── detectors/           # 检测器（anomaly/startup/false_start/continuation/withdrawal）
├── health/              # 数据健康（freshness/confidence/coverage 覆盖率）  ← V1.1 + V1.3
├── market/              # 市场背景 Regime + 结构/Volume Profile/Location    ← V1.2
├── state_machine/       # 状态机（SLEEPING→ANOMALY→SUSPECTED→CONFIRMED→...）
├── supervision/         # 状态监督（8 池 + 滞回 + 生命周期）                ← V1.3 P1
├── simulation/          # 模拟验证（快照/队列/重验证/持仓/动态退出/统计）    ← V1.3 P2
├── scoring/             # 评分引擎（11 子评分 + 机会分 + 置信度引擎）
├── presentation/        # 翻译层 + Top10 排名
├── storage/             # SQLite 持久化 + 内存仓库                            ← V1.2
├── recovery/            # RecoveryManager 三档停机恢复 + SystemMode          ← V1.2
├── replay/              # Replay Calibration（分桶校准）                      ← V1.2
├── runtime.py           # 运行时编排（Radar + 防抖 + 评分 + 快照 + 模拟集成）
└── main.py              # FastAPI 入口 + 静态文件托管

static/                  # 前端 SPA（V1.3 5 页重构）
├── index.html
├── css/style.css
└── js/ (api.js, app.js)

configs/                 # 全部配置（app/symbols/features/detectors/state_machine/
                        #   data_health/hysteresis/scoring/ranking/supervision/
                        #   simulation/health_coverage）
tests/                   # 728 测试（scoring/presentation/supervision/simulation/api）
scripts/                 # live_smoke_test.py / ui_seed_server.py（UI 冒烟夹具）
docs/                    # 设计文档 + UI.md
```

---

## 配置

| 文件 | 说明 |
|---|---|
| `configs/app.yaml` | 交易所、代理、编排节奏 |
| `configs/symbols.yaml` | Universe 过滤（quote_asset/blacklist/whitelist/liquidity/top_n） |
| `configs/features.yaml` | 窗口、Kline 周期、基线样本 |
| `configs/detectors.yaml` | 检测器 + Stage1 增量阈值 |
| `configs/hysteresis.yaml` | 候选防抖（驻留时间/连续跌出/上限） |
| `configs/scoring.yaml` | 评分权重 + 置信度因子 |
| `configs/state_machine.yaml` | 状态机参数 |
| `configs/data_health.yaml` | freshness budget + rate limiter |
| `configs/ranking.yaml` | Top 排名阈值 + UI 刷新节奏 ← V1.3 新增 |
| `configs/supervision.yaml` | 监督池规则/级别/滞回 ← V1.3 新增 |
| `configs/simulation.yaml` | 模拟验证参数（Entry Zone/TP/Stop/24h 上限）← V1.3 新增 |
| `configs/health_coverage.yaml` | 覆盖率阈值（90/70）+ 核心流优先级 ← V1.3 新增 |

---

## 文档导航

### 顶层设计

| 文档 | 内容 |
|------|------|
| [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) | 系统设计规范 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 分层架构、事件拓扑 |
| [AI_CODING_AGENT_MANUAL.md](AI_CODING_AGENT_MANUAL.md) | AI 开发执行手册 |
| [AI_RULES.md](AI_RULES.md) | 硬规则 + 方向安全 |

### 数据与模型契约

| 文档 | 内容 |
|------|------|
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | 事件对象字段、枚举 |
| [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) | 状态转移、guard |
| [docs/DATA_HEALTH.md](docs/DATA_HEALTH.md) | freshness、ConfidenceState、覆盖率 |
| [docs/ANALYSIS_MODEL.md](docs/ANALYSIS_MODEL.md) | 特征、证据、Veto |
| [docs/FEATURE_CATALOG.md](docs/FEATURE_CATALOG.md) | Feature 目录 |
| [docs/DETECTOR_LOGIC.md](docs/DETECTOR_LOGIC.md) | 检测器判定逻辑 |
| [docs/UI.md](docs/UI.md) | UI 架构（5 页 + Drawer + 轮询节奏）← V1.3 新增 |

### Runtime 文档

| 文档 | 内容 |
|------|------|
| [docs/RUNTIME_ARCHITECTURE.md](docs/RUNTIME_ARCHITECTURE.md) | 运行时编排 |
| [docs/LIVE_DATA_FLOW.md](docs/LIVE_DATA_FLOW.md) | 端到端数据流 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 启动/配置/运维 |
| [docs/RUNTIME_INTEGRATION_AUDIT.md](docs/RUNTIME_INTEGRATION_AUDIT.md) | 运行时集成审计 |
| [docs/TESTING.md](docs/TESTING.md) | 测试体系 |
| [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | 已知限制 |

### 版本计划

| 文档 | 内容 |
|------|------|
| [资金行为雷达_V1.1_Runtime修复_评分体系_Top10大屏_UI产品化.md](资金行为雷达_V1.1_Runtime修复_评分体系_Top10大屏_UI产品化.md) | V1.1 完整计划（19 步骤） |
| [资金行为雷达_V1.2_资金生命周期_结构位置_置信度_TradePlan_修改方案.md](资金行为雷达_V1.2_资金生命周期_结构位置_置信度_TradePlan_修改方案.md) | V1.2 完整计划 |
| [资金行为雷达_V1.3_状态监督_模拟验证_UI重构_更新计划.md](资金行为雷达_V1.3_状态监督_模拟验证_UI重构_更新计划.md) | V1.3 完整计划（§1-§72） |

---

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.12 + asyncio + FastAPI + Pydantic |
| 前端 | 原生 HTML/CSS/JS（SPA，FastAPI 静态托管，无构建系统） |
| 持久化 | SQLite（`src/storage`） |
| 测试 | pytest + pytest-asyncio + hypothesis |
| 部署 | uvicorn（单进程）；Docker Compose（规模上升后） |

---

## 许可与合规

- 默认只使用公开市场数据，不要求用户 Binance API Key。
- UI 中"主力资金/资金进入"仅为通俗表述，详情页必须解释为统计推断，不是识别具体账户。
- 所有提醒为行情分析信号，不保证收益，不自动执行交易。