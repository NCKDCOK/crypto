# 资金行为驱动行情分析系统

> 全市场扫描 Binance USDT-M 永续合约"钱是否正在异常进入/退出"，先过滤假启动，再提醒用户关注真正有持续性的资金行为。
>
> **只读公开市场数据，只做分析提醒，不自动交易。**

---

## 这是什么

一个资金行为驱动的实时行情分析与启动雷达系统。核心研究对象不是 RSI/MACD，而是 **Price × Volume × Trade Flow × Open Interest** 的联动：识别资金异动、启动、延续、衰竭与撤离，输出可解释证据、排行榜和提醒。

| | |
|---|---|
| **核心原则** | 先证明"数据可信"和"资金行为可解释"，再谈评分与 AI 解读 |
| **Evidence-first** | V1 只输出证据链 + 状态，不搞拍脑袋的 0–100 机会分 |
| **Fail Closed** | 数据不新鲜/不完整时输出 UNKNOWN / DATA_STALE，不继续造信号 |
| **不做什么** | 自动下单、API Key、账户、仓位、杠杆、订单管理 |

---

## 项目状态

📋 **纯文档/规划阶段** — 控制文件已就绪，代码零起步。按 Gate 流程推进。

---

## 文档导航

### 顶层设计

| 文档 | 内容 |
|------|------|
| [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) | 系统设计规范（架构、契约、检测器、状态机） |
| [AI_CODING_AGENT_MANUAL.md](AI_CODING_AGENT_MANUAL.md) | AI Coding Agent 开发执行手册（Gate 流程、任务格式） |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 分层架构、事件拓扑、时钟、存储与配置接口 |
| [AI_RULES.md](AI_RULES.md) | 12 条硬规则 + 方向安全 P0 规则（每个 Agent 必读） |

### 数据与模型契约

| 文档 | 内容 |
|------|------|
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | 全部事件对象字段、枚举、aggressor_side 映射、OI 单位 |
| [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) | 完整状态转移、guard、COOLDOWN 配置、squeeze 例外 |
| [docs/DATA_HEALTH.md](docs/DATA_HEALTH.md) | freshness budget、HealthLevel、ConfidenceState 派生、限频 |
| [docs/ANALYSIS_MODEL.md](docs/ANALYSIS_MODEL.md) | 特征清单、证据族、Veto 清单、检测器职责 |
| [docs/TESTING.md](docs/TESTING.md) | 测试策略、fixture 规范、replay 确定性 |

### Epic Specs（按 Gate 推进）

| Spec | Gate | 目标 |
|------|------|------|
| [specs/epic-00-foundation.md](specs/epic-00-foundation.md) | 0 | 项目骨架 / contracts / 测试框架 |
| [specs/epic-01-market-data.md](specs/epic-01-market-data.md) | 1 | Market Data Gateway |
| [specs/epic-02-data-health.md](specs/epic-02-data-health.md) | 2 | Data Health & 时间对齐 |
| [specs/epic-03-feature-engine.md](specs/epic-03-feature-engine.md) | 3 | Feature Engine |
| [specs/epic-04-anomaly.md](specs/epic-04-anomaly.md) | 4 | Anomaly Detector |
| [specs/epic-05-startup.md](specs/epic-05-startup.md) | 5 | Startup 候选/确认 |
| [specs/epic-06-false-start.md](specs/epic-06-false-start.md) | 6 | False Start Filter（最重要） |
| [specs/epic-07-continuation-withdrawal.md](specs/epic-07-continuation-withdrawal.md) | 7 | Continuation & Withdrawal |
| [specs/epic-08-replay-labeling.md](specs/epic-08-replay-labeling.md) | 8 | Replay & 标注 |
| [specs/epic-09-dashboard-alerts.md](specs/epic-09-dashboard-alerts.md) | 9 | Dashboard & Alerts |

### 决策记录

| ADR | 标题 |
|-----|------|
| [docs/adr/0001-record-architecture-decisions.md](docs/adr/0001-record-architecture-decisions.md) | 记录架构决策 |
| [docs/adr/0000-template.md](docs/adr/0000-template.md) | ADR 模板 |

---

## 推荐技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.12 + asyncio + FastAPI + Pydantic |
| 缓存/队列 | 进程内 asyncio.Queue（V1）；Redis Streams（规模上升后） |
| 数据库 | PostgreSQL → TimescaleDB |
| 前端 | React + TypeScript + Vite |
| 测试 | pytest + pytest-asyncio + hypothesis |
| 部署 | Docker Compose |

---

## 开发方式

采用 **Gate-based AI Development**：进度不以"第几周"为主，而以"通过验收 Gate 后才进入下一阶段"为主。

- **人负责**：需求、证据定义、边界与验收。
- **AI 负责**：在锁定接口和测试条件下实现代码。
- **绝不让 AI 同时充当研究员、架构师、程序员并自由发明交易逻辑。**

推荐会话分工：Agent A = Implementer / Agent B = Reviewer / Agent C = Test Designer。

详见 [AI_CODING_AGENT_MANUAL.md](AI_CODING_AGENT_MANUAL.md)。

---

## 开工顺序

| # | Task | 完成标志 |
|---|------|----------|
| 01 | domain contracts + configs + test harness | 仓库骨架与稳定事件模型 |
| 02 | aggTrade collector + reconnect + dedup | 实时成交流，不做分析 |
| 03 | Data Health freshness watchdog | 能识别"WS 连着但没数据" |
| 04 | OI poller + time-aligned history | OI contracts 变化可信 |
| 05 | Taker Delta + windowed CVD | 真正的主动买卖资金流 |
| 06 | Volume/TradeCount/PriceAccel robust anomaly | 全市场异动候选 |
| 07 | Startup evidence model | SUSPECTED_START |
| 08 | False-start veto pack | REJECTED vs START_CONFIRMED |
| 09 | Continuation / Withdrawal | 资金持续与撤离状态 |
| 10 | Replay + labels | 开始验证系统有没有 edge |
| 11 | Dashboard / Telegram | 最后再做漂亮展示 |

---

## 许可与合规

- 默认只使用公开市场数据，不要求用户 Binance API Key。
- UI 中"主力资金/资金进入"仅为通俗表述，详情页必须解释为统计推断，不是识别具体账户。
- 所有提醒为行情分析信号，不保证收益，不自动执行交易。
