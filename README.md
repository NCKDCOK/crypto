# 资金行为雷达 — Crypto Market Radar

> 全市场扫描 Binance USDT-M 永续合约"钱是否正在异常进入/退出"，先过滤假启动，再提醒用户关注真正有持续性的资金行为。
>
> **只读公开市场数据，只做分析提醒，不自动交易。**

---

## 这是什么

一个资金行为驱动的实时行情分析与启动雷达系统。核心研究对象不是 RSI/MACD，而是 **Price × Volume × Trade Flow × Open Interest** 的联动：识别资金异动、启动、延续、衰竭与撤离，输出可解释评分、Top10 排行和用户化提醒。

系统核心流程：

```
资金异动 → 疑似启动 → 假启动过滤 → 确认启动 → 趋势延续 → 衰竭 → 撤离
```

| | |
|---|---|
| **核心原则** | 先证明"数据可信"和"资金行为可解释"，再谈评分与 AI 解读 |
| **Evidence-first** | 任何分数必须可展开 → 原始 Evidence → 原始数据 |
| **可解释评分** | 11 个子评分 + 机会分 + 独立置信度，权重全部配置化 |
| **Fail Closed** | 数据不新鲜/不完整时输出 UNKNOWN / DATA_STALE，不继续造信号 |
| **不做什么** | 自动下单、API Key、账户、仓位、杠杆、订单管理 |

---

## 项目状态

🟢 **V1.1 已落地 — Runtime 修复 + 评分体系 + Top10 大屏 + UI 产品化**

### V1.1 新增

| 模块 | 说明 |
|---|---|
| **Runtime 修复 (P0)** | Stage1 增量异动、多周期 Kline、候选防抖、数据状态翻译、Dashboard 统一 |
| **评分引擎** | 11 个子评分（7 基础 + 4 风险），OpportunityScore = 加权基础分 - 风险扣分 |
| **置信度引擎** | 独立于机会分，受数据健康/证据完整性/多窗口一致性影响 |
| **翻译层** | PresentationTranslator：内部术语 → 用户中文（状态/方向/资金行为/量价/假启动） |
| **Top10 排名** | RankingScore = OpportunityScore × ConfidenceFactor，排除 UNKNOWN/stale |
| **UI 大屏** | 6 页面 SPA：首页 Top10 / 全市场搜索 / 详情 / 信号中心 / 数据健康 / 回放 |

### 已 Live 的数据

| 数据源 | 状态 | 说明 |
|---|---|---|
| aggTrade (WS) | ✅ LIVE | 实时成交、aggressor side、CVD、taker delta、trade_id 去重 |
| Kline 1m/5m/15m/1h (WS) | ✅ LIVE | 多周期订阅（V1.1 修复：之前仅 1m） |
| Open Interest (REST) | ✅ LIVE | 基础资产数量、Δ30s/1m/5m、velocity、accel |
| Funding/Premium (REST) | ✅ LIVE | 拥挤度上下文、soft veto |
| 24h ticker (REST) | ✅ LIVE | 动态 universe 发现 + Stage1 短时增量扫描 |

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
pytest -q                                          # 全量离线测试（363 passed）
python scripts/live_smoke_test.py --duration 600   # 10 分钟 live 冒烟（BTC/ETH/SOL）
python scripts/v11_smoke_test.py                   # V1.1 评分引擎端到端验证
```

---

## UI 页面

| 页面 | 路由 | 功能 |
|---|---|---|
| **首页 / 资金雷达大屏** | `#/` | Top10 排名（Hero Card + 标准卡片）+ 系统结论 + Market Summary |
| **全市场** | `#/market` | 搜索 + 状态筛选 + 多维排序 + 全量表格 |
| **币种详情** | `#/symbol/:symbol` | 评分 breakdown + 组件明细 + 资金行为 + 量价 + 假启动检查 + 证据链 + 状态时间轴 |
| **信号中心** | `#/signals` | 历史状态变化（中文翻译） |
| **数据健康** | `#/health` | 开发者表（每流 freshness + 置信度） |
| **回放验证** | `#/replay` | 评分校准（需积累数据后启用） |

UI 风格：**科技感 + Apple 式克制丝滑** — 深色背景、冷青主色、大留白、柔和圆角、150-300ms 动效、响应式、`/` 快捷键搜索。

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
├── collectors/          # 数据采集器（aggTrade/Kline/OI/Funding WS+REST）
├── features/            # 特征引擎（多窗口/基线/z-score/CVD/效率）
├── detectors/           # 检测器（anomaly/startup/false_start/continuation/withdrawal）
├── health/              # 数据健康（freshness/confidence/rate_limiter/queue_lag）
├── state_machine/       # 状态机（SLEEPING→ANOMALY→SUSPECTED→CONFIRMED→...）
├── scoring/             # 评分引擎（11子评分 + 机会分 + 置信度引擎）  ← V1.1 新增
├── presentation/        # 翻译层 + Top10 排名                      ← V1.1 新增
├── api/                 # FastAPI（已 DEPRECATED，统一到 main.py）
├── runtime.py           # 运行时编排（两阶段 Radar + 防抖 + 评分集成）
└── main.py              # FastAPI 入口 + 静态文件托管

static/                  # 前端 SPA                                ← V1.1 新增
├── index.html
├── css/style.css
└── js/ (api.js, app.js)

configs/                 # 全部配置（app/symbols/features/detectors/
                        #          state_machine/data_health/hysteresis/scoring）
tests/                   # 363 测试（含 scoring + presentation）
```

---

## 配置

| 文件 | 说明 |
|---|---|
| `configs/app.yaml` | 交易所、代理、编排节奏 |
| `configs/symbols.yaml` | Universe 过滤（quote_asset/blacklist/whitelist/liquidity/top_n） |
| `configs/features.yaml` | 窗口、Kline 周期、基线样本 |
| `configs/detectors.yaml` | 检测器 + Stage1 增量阈值 |
| `configs/hysteresis.yaml` | 候选防抖（驻留时间/连续跌出/上限） ← V1.1 新增 |
| `configs/scoring.yaml` | 评分权重 + 置信度因子 ← V1.1 新增 |
| `configs/state_machine.yaml` | 状态机参数 |
| `configs/data_health.yaml` | freshness budget + rate limiter |

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
| [docs/DATA_HEALTH.md](docs/DATA_HEALTH.md) | freshness、ConfidenceState |
| [docs/ANALYSIS_MODEL.md](docs/ANALYSIS_MODEL.md) | 特征、证据、Veto |
| [docs/FEATURE_CATALOG.md](docs/FEATURE_CATALOG.md) | Feature 目录 |

### Runtime 文档

| 文档 | 内容 |
|------|------|
| [docs/RUNTIME_ARCHITECTURE.md](docs/RUNTIME_ARCHITECTURE.md) | 运行时编排 |
| [docs/LIVE_DATA_FLOW.md](docs/LIVE_DATA_FLOW.md) | 端到端数据流 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 启动/配置/运维 |
| [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | 已知限制 |

### V1.1 计划

| 文档 | 内容 |
|------|------|
| [资金行为雷达_V1.1_Runtime修复_评分体系_Top10大屏_UI产品化.md](资金行为雷达_V1.1_Runtime修复_评分体系_Top10大屏_UI产品化.md) | V1.1 完整计划（19 步骤） |

---

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.12 + asyncio + FastAPI + Pydantic |
| 前端 | 原生 HTML/CSS/JS（SPA，FastAPI 静态托管，无构建系统） |
| 测试 | pytest + pytest-asyncio + hypothesis |
| 部署 | uvicorn（单进程）；Docker Compose（规模上升后） |

---

## 许可与合规

- 默认只使用公开市场数据，不要求用户 Binance API Key。
- UI 中"主力资金/资金进入"仅为通俗表述，详情页必须解释为统计推断，不是识别具体账户。
- 所有提醒为行情分析信号，不保证收益，不自动执行交易。
