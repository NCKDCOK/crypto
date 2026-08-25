**资金行为驱动行情分析系统**

**AI Coding Agent 开发执行手册**

| **版本**     | v1.0                                                 |
|--------------|------------------------------------------------------|
| **日期**     | 2026-08-25                                           |
| **定位**     | 资金行为驱动的实时行情分析与启动雷达；不执行自动交易 |
| **开发模式** | Spec-driven / Gate-based AI Development              |

**核心原则：先证明“数据可信”和“资金行为可解释”，再谈评分与 AI 解读。**

# 0. 为什么开发路线要改成 Gate，而不是“第几周”

当 Codex / Claude Code / Cursor 等 AI Agent 是主要开发者时，速度不可预测：简单模块可能数小时完成，数据边界问题可能卡几天。因此进度不应以“8–10 周”为主，而应以“通过验收 Gate 后才能进入下一阶段”为主。

|     | **核心工作方式** 人负责：需求、证据定义、边界与验收；AI 负责：在锁定接口和测试条件下实现代码。绝不让 AI 同时充当研究员、架构师、程序员并自由发明交易逻辑。 |
|-----|------------------------------------------------------------------------------------------------------------------------------------------------------------|

# 1. 仓库必须先准备的控制文件

/docs  
SYSTEM_DESIGN.md  
DATA_MODEL.md  
ANALYSIS_MODEL.md  
STATE_MACHINE.md  
DATA_HEALTH.md  
TESTING.md  
  
/specs  
epic-00-foundation.md  
epic-01-market-data.md  
epic-02-data-health.md  
epic-03-feature-engine.md  
epic-04-anomaly.md  
epic-05-startup.md  
epic-06-false-start.md  
epic-07-continuation-withdrawal.md  
epic-08-replay-labeling.md  
epic-09-dashboard-alerts.md  
  
AI_RULES.md  
ARCHITECTURE.md  
README.md

每个 Agent 开始任务前必须阅读 AI_RULES.md、ARCHITECTURE.md 和当前 epic spec；不得只读用户的一句自然语言就直接改代码。

# 2. AI_RULES.md 必须包含的硬规则

1\. 本项目不是自动交易系统。禁止实现下单、API Key、账户、仓位、杠杆和订单管理。

2\. 核心目标是资金行为分析；未经 spec 明确要求，不得主动加入 RSI、MACD、EMA、OB、FVG 等指标。

3\. 数据异常必须 fail closed：宁可 UNKNOWN / DATA_STALE，不得用陈旧数据继续产生确认信号。

4\. 所有阈值配置化，禁止把 magic number 散落在业务代码。

5\. 所有状态变化必须产生 machine-readable evidence 与 veto，禁止只返回一个 score。

6\. AI 不得自行决定权重，不得自行创造“机会分公式”。

7\. Collector 只能采集/标准化；Feature 只能计算；Detector 只能分析；UI 不能复制业务逻辑。

8\. 已验收接口视为稳定 contract；未经 ADR（Architecture Decision Record）不得大规模重构。

9\. 新增核心逻辑必须同时提交单元测试、fixture 或 replay test。

10\. 不得通过降低测试标准、删除断言、扩大 tolerance 来“修复”失败。

11\. 外部开源代码必须记录来源、许可证和修改点；不能把“参考”当成“复制”。

12\. 每次任务只完成当前 spec，不顺手增加未请求功能。

13\. **aggressor_side 映射不可写反。** Binance aggTrade `m=true` ⇒ 买方是 maker ⇒ 卖方主动 ⇒ `aggressor_side=SELL`。任何实现必须对此写单元测试。写反则 CVD/Taker Delta 全量反转。

14\. **OI 用基础资产数量（open_interest），不用美元名义。** 价格涨但 open_interest 不变 ⇒ oi_change=0。不得把名义 OI 上涨误判为新增仓位。

15\. **connected ≠ healthy。** WS socket open 但无数据推送必须被 freshness watchdog 降级为 STALE，不得当作健康。

16\. **trade_id 去重必须在进入 Feature Engine 前完成。** 重连后重复成交不得污染 CVD。

# 3. 每个 AI Task 的标准格式

| **字段**           | **要求**                                    |
|--------------------|---------------------------------------------|
| Goal               | 一句话定义本任务唯一目标                    |
| Context            | 引用相关设计文档与已存在 contracts          |
| Inputs             | 允许读取的数据/接口                         |
| Outputs            | 必须实现的类、函数、事件、API               |
| Invariants         | 不得破坏的系统约束                          |
| Out of Scope       | 本任务明确禁止做什么                        |
| Tests              | 必须新增/通过哪些测试                       |
| Observability      | 日志、metric、错误状态要求                  |
| Definition of Done | 可客观判断的完成标准                        |
| Files Allowed      | 最好限定可改文件范围，避免 Agent 扫荡全仓库 |

|     | **任务粒度** 一个 Task 最好能在单次 Agent 会话中实现和审查。不要给“实现整个 Binance 数据系统”这种大任务。 |
|-----|-----------------------------------------------------------------------------------------------------------|

# 4. Gate 0 — Foundation / 项目骨架

**目标：**先锁定 contracts、目录、配置与测试框架，不接真实交易所。

- 创建 domain events：TradeEvent、KlineEvent、OpenInterestSnapshot、FundingRateSnapshot、HealthStatus、FeatureSnapshot、AnalysisEvent，以及全部枚举（AggressorSide、Direction、ConfidenceState、HealthLevel、State、EvidenceFamily、VetoType）。

- 创建 config 模型与环境配置；阈值全部集中管理。

- 设置 pytest、pytest-asyncio、lint/type-check、CI。

- 建立 structured logging 与 clock abstraction（真实时间/测试时间可替换）。

- 创建 fixtures/ 与 replay harness 的空骨架。

| **DoD**                                | **验收方式**             |
|----------------------------------------|--------------------------|
| 领域对象序列化/反序列化稳定            | contract tests           |
| 没有任何 Binance 网络调用              | 离线测试可全部运行       |
| CI 绿色                                | lint + test + type check |
| AI_RULES / ARCHITECTURE / ADR 模板入库 | 人工审查                 |

给 AI 的任务提示示例：  
“实现 Gate 0 的 domain contracts。只允许修改 src/domain、tests/contracts 和 configs。  
不要实现任何 Binance collector、指标、Detector 或 UI。  
严格按 SYSTEM_DESIGN.md 第5节字段创建 Pydantic 模型，并为时间字段、枚举和 JSON round-trip 写测试。”

# 5. Gate 1 — Market Data Gateway

**目标：**把公开 Binance Futures 数据稳定地变成统一事件；此阶段不计算 CVD、不判断启动。

| **Task**               | **实现**                                                | **测试/验收**                           |
|------------------------|---------------------------------------------------------|-----------------------------------------|
| 1.1 Symbol Registry    | USDT-M 交易对发现、过滤下架/无效 symbol                 | 固定 fixture 能得到预期列表             |
| 1.2 aggTrade Collector | WS 连接、订阅、解析、aggressor side、event/receive time | 连续运行；强制断线恢复；trade_id 不重复 |
| 1.3 Kline Collector    | 1m closed kline + 必要实时 kline                        | 仅 closed bar 标记正确                  |
| 1.4 OI Poller          | open_interest 快照（基础资产数量）、统一时间戳                              | 速率受控；错误不阻塞其他 symbol         |
| 1.5 Funding/Premium    | 资金费率、mark/premium                                  | 字段单位和时间戳有 fixture 测试         |
| 1.6 REST RateLimiter   | 权重、Retry-After、backoff、circuit breaker             | 模拟 429，不产生 retry storm            |

|     | **本 Gate 禁止** 禁止 CVD、OI 增速、信号、机会分、数据库分析逻辑、UI。先证明数据输入可控。 |
|-----|--------------------------------------------------------------------------------------------|

# 6. Gate 2 — Data Health & Time Alignment

**目标：**解决“WS 看似连接但数据不推”和 REST 时间错位问题。

- 每个 stream 独立 freshness budget，connected 与 healthy 分开。

- trade_id 去重；若未来加入 depth，必须校验 sequence。

- OI 通过 as-of lookup 找 1m/5m/15m 前快照，必须带最大时间容差。

- 检测队列积压、receive lag、event lag、reconnect count。

- HealthStatus 通过统一事件暴露给 Feature Engine 与 UI。

| **必须场景**                          | **预期**                                     |
|---------------------------------------|----------------------------------------------|
| WS socket 仍 open，但 30s 无 aggTrade | 对应 stream → STALE                          |
| 重连后重复一笔 trade                  | 丢弃重复，CVD 前置数据不双计                 |
| 5m 前没有足够接近的 OI 快照           | oi_change_5m = unavailable，而非取 9m 前数据 |
| 429 返回 Retry-After                  | 全局限流按策略退避；不让其他模块各自重试     |
| 关键数据 STALE                        | 后续 detector 无权进入 CONFIRMED             |

Reviewer Prompt：  
“只审查 Gate 2。寻找所有会导致 stale 数据仍被当作 healthy、重复成交污染、时间基线错配、429 retry storm 的路径。  
不要建议新指标或 UI 功能。给出具体文件/行级修改建议，并说明哪条 invariant 被违反。”

# 7. Gate 3 — Feature Engine

**目标：**把“原始行情”变成可验证的资金行为特征；仍不生成 START_CONFIRMED。

| **Task**             | **首批特征**                                 | **验收**                                |
|----------------------|----------------------------------------------|-----------------------------------------|
| 3.1 Rolling Windows  | 5s/15s/30s/1m/5m 滚动窗口                    | 边界时间测试、窗口淘汰测试              |
| 3.2 Volume/Trades    | RVOL、robust Z、trade count Z                | 手算 fixture 一致                       |
| 3.3 Taker Flow       | buy/sell notional、net delta                 | 逐笔 fixture 一致                       |
| 3.4 CVD              | windowed CVD、slope、acceleration            | 重连/重复事件不污染                     |
| 3.5 OI Features      | Δ、velocity、acceleration（open_interest，基础资产数量） | 价格变动但 open_interest 不变时 OI change=0 |
| 3.6 Price Efficiency | directional efficiency、flow impact、retrace | 合成路径可手工验证                      |
| 3.7 Context          | funding/premium percentile                   | 只能 context，不产生 signal             |

|     | **关键验收** 每个 Feature 都必须能回答：输入是什么、公式是什么、窗口是什么、缺数据时是什么、fixture 怎么复算。 |
|-----|----------------------------------------------------------------------------------------------------------------|

# 8. Gate 4 — Anomaly Detector

**目标：**高召回发现“异动候选”，不负责判断真假。

- 基于 robust z-score / percentile，不先硬编码“成交量 3 倍就是启动”。

- 输出 AnomalyEvidence 列表与 direction_hint（可为空）。

- 保留触发前/后的 feature snapshot 以便 replay。

- 所有阈值来自配置；AI 不得自己调优。

| **测试场景**                      | **期望**                            |
|-----------------------------------|-------------------------------------|
| 正常随机噪声                      | 大多数时间不触发                    |
| volume+trade count 同步尖峰       | 触发 volume anomaly                 |
| 单笔超大成交但 trade count 无变化 | 证据体现差异，不直接等同真启动      |
| 关键数据 stale                    | 不得发可升级到 confirmed 的 anomaly |

# 9. Gate 5 — Startup Candidate / Confirm

**目标：**从异常中建立“方向 + 新增资金 + 价格效果”的证据链。

Evidence Families：  
A. 异常：Volume / TradeCount / PriceAccel  
B. 主动资金：Taker Delta / CVD  
C. 仓位：OI expansion / contraction classification  
D. 价格效果：Efficiency / Breakout / Acceptance  
E. 上下文：Funding / Premium（不单独触发）

- 先输出 SUSPECTED_START，再由确认条件升级；禁止单次 spike 直接确认。

- 明确区分“新增多头启动”和“空头回补 squeeze”；二者可以都上涨，但证据类别不同。

- 方向必须从数据推断，不能从 UI 选择或 AI 文本反向写入。

|     | **禁止 AI 自由发挥** 本 Gate 的 spec 只允许实现已定义 evidence family；Agent 不得自行加 RSI、MACD、Fibonacci，也不得发明权重。 |
|-----|--------------------------------------------------------------------------------------------------------------------------------|

# 10. Gate 6 — False Start Filter（最重要）

**目标：**把“异动很多”变成“只留下有持续性证据的候选”。

| **Veto**                         | **最小实现**                                 | **测试 fixture**           |
|----------------------------------|----------------------------------------------|----------------------------|
| Data Stale                       | 任一 hard-required 输入 stale → hard veto    | 冻结 aggTrade 后尝试确认   |
| Rapid Retrace                    | 候选后回吐比例超过配置条件                   | 先拉升后完整回吐           |
| OI Contraction                   | 方向上涨但 OI 收缩 → squeeze 分类/非新增资金 | price↑, volume↑, OI↓       |
| Delta Reversal                   | 初始同向 delta 后持续反向                    | 正 delta → 强负 delta      |
| No Acceptance                    | 突破未在新区域停留                           | 刺穿后立即回区间           |
| Low Efficiency / Absorption Risk | 巨大主动资金但价格推不动                     | delta 高、flow impact 极低 |
| Crowding Extreme                 | Funding/Premium 极端且追涨 → soft veto       | funding 极正 + 追涨       |

Gate 6 完成后，不要急着“优化命中率”。先持续记录 REJECTED 与 CONFIRMED 样本，建立真实标签集。

# 11. Gate 7 — Continuation & Withdrawal

**目标：**实现“主力进我跟，主力走我知道”的后半段。

| **Detector** | **关注证据**                                           | **输出**                          |
|--------------|--------------------------------------------------------|-----------------------------------|
| Continuation | OI 持续、CVD 同向、回踩卖压减弱、效率健康、二次接受    | CONTINUATION / WEAKENING evidence |
| Exhaustion   | 价格创新高但 CVD/OI/效率不确认、成交量大而推动变差     | EXHAUSTION                        |
| Withdrawal   | OI 收缩 + delta/CVD 反转 + 主动卖出持续 + 价格结构失守 | WITHDRAWAL                        |

|     | **独立模型** 撤离不是“启动条件取反”。必须单独编写 WithdrawalDetector 和自己的 fixture。 |
|-----|-----------------------------------------------------------------------------------------|

# 12. Gate 8 — Replay、Labeling 与统计

**目标：**把系统从“看起来厉害”变成“能被历史样本验证”。

- 事件时间顺序重放；支持固定 clock；结果 deterministic。

- 保存每次状态转换前后的 FeatureSnapshot。

- 为每个候选生成 outcome：最大有利/不利变动、持续时间、回撤、是否快速失败。

- 允许人工标注 false_start / continuation / squeeze / absorption / withdrawal。

- 建立简单报表：每个 veto 命中后的后续表现、每个 evidence 的条件频率。

|     | **只有到这里以后** 才允许讨论机会分、权重、概率和“哪个因子最重要”。在此之前任何 0–100 分都只是人为装饰。 |
|-----|----------------------------------------------------------------------------------------------------------|

# 13. Gate 9 — Dashboard & Alerts

**目标：**把分析结果做成“市场雷达”，但 UI 绝不能重新实现业务逻辑。

| **页面**       | **V1 内容**                                                           |
|----------------|-----------------------------------------------------------------------|
| Market Radar   | symbol、price、24h、state、direction、data health、关键 evidence 摘要 |
| Symbol Detail  | 时间线、特征、Evidence、Veto、State transition、OI/CVD/Delta 图       |
| Data Health    | 每个 stream 的 freshness、reconnect、message rate、429/REST 状态      |
| Signal History | CONFIRMED / REJECTED / WITHDRAWAL 历史与 outcome                      |
| Alert          | START_CONFIRMED、EXHAUSTION、WITHDRAWAL；可配置阈值与冷却             |

AI 解读只能读取 AnalysisEvent 的结构化结果，把 evidence 翻译成人话；不得让 LLM 重新决定方向或覆盖状态机。

# 14. Gate 10 — Hardening / 服务器长期运行

- 断网、DNS、WS 24h 重连、服务器时间偏差、进程重启恢复。

- 队列背压：Feature Engine 跟不上时要丢弃/降级还是阻塞，必须有明确策略。

- REST 全局权重预算和 429/418 监控。

- 数据库写入失败不能拖死 collectors；支持缓冲或降级。

- 内存上限、symbol 数量、rolling window 数量与数据保留策略。

- Docker healthcheck、自动重启、metrics、日志轮转。

| **长期运行测试**  | **最低要求**                                           |
|-------------------|--------------------------------------------------------|
| 24h soak test     | 无内存持续增长；重连后数据恢复；状态不乱跳             |
| 故障注入          | 断网、429、WS freeze、DB unavailable 均有预期降级      |
| Replay regression | 升级代码后历史 fixture 状态序列无意外变化              |
| 性能              | 覆盖目标 symbol 集时队列延迟在预算内；不得靠无界内存撑 |

# 15. 推荐的 Agent 工作循环

1\. 选择一个明确 Task，先让 Agent 只读相关 spec 和现有代码，输出实施计划。

2\. 人确认计划没有改变研究逻辑/接口边界，再允许 Agent 编码。

3\. Agent 实现代码 + 测试，必须在本地运行完整相关 test suite。

4\. 启动第二个“Reviewer Agent”，只做审查：数据正确性、边界条件、stale/rate-limit/replay 风险。

5\. 修复 review findings；再次跑测试。

6\. 通过 Gate 验收后锁定 contracts，更新 CHANGELOG / ADR，再进入下一 Task。

推荐会话分工：  
  
Agent A = Implementer（只完成当前 task）  
Agent B = Reviewer（不写新功能，只找错误）  
Agent C = Test Designer（针对边界/故障设计 fixture）  
  
不要让同一个 Agent 连续自由扩展整个项目数十个文件。

# 16. 通用 Coding Agent Prompt 模板

你是本仓库的实现 Agent。  
  
任务：\<Task ID + Goal\>  
必须先阅读：AI_RULES.md、ARCHITECTURE.md、\<当前 spec\>。  
  
允许修改：\<files/directories\>  
禁止修改：\<files/directories\>  
  
输入 contracts：\<...\>  
必须输出：\<...\>  
必须保持 invariants：\<...\>  
Out of scope：\<...\>  
  
测试要求：  
1) ...  
2) ...  
3) ...  
  
执行步骤：  
- 先只读代码并给出不超过 10 条实施计划；不要立即写代码。  
- 计划通过后再实现。  
- 不要新增未请求功能。  
- 不得通过删除/放宽测试来让 CI 通过。  
- 完成后列出改动文件、测试命令、测试结果和仍存在的风险。

# 17. Reviewer Agent Prompt 模板

你是独立 Reviewer，不负责实现新功能。  
只审查 \<Task ID\> 相对 spec 的正确性。  
  
优先寻找：  
- stale 数据仍被使用  
- 时间窗口/时间戳错位  
- duplicate trade / reconnect 污染  
- OI 单位错误（基础资产数量 vs 美元名义）  
- 429/418 retry storm  
- 队列阻塞/泄漏  
- magic number  
- 状态转换无 evidence  
- 测试只覆盖 happy path  
  
输出按严重程度分 P0/P1/P2，并给出具体文件和触发场景。  
不要建议 RSI、MACD、自动下单或超出本 Task 的功能。

# 18. Bug 修复协议

1\. 先把 bug 写成最小失败 fixture / regression test。

2\. 确认问题属于哪一层：collector / health / feature / detector / state machine / UI。

3\. 只在责任层修复，禁止在 UI 用 if 临时掩盖后端错误。

4\. 若要改变 contract 或算法定义，先更新 spec/ADR，再改代码。

5\. 修复后跑当前层测试 + replay regression，防止一个 bug 修复破坏历史行为。

# 19. AI 最容易犯的错误清单

| **错误**                    | **为什么危险**           | **预防**                 |
|-----------------------------|--------------------------|--------------------------|
| 一口气实现整个系统          | 接口漂移、模块互相抄逻辑 | 任务切碎 + Gate          |
| 擅自加入传统指标            | 偏离资金行为核心         | AI_RULES + Out of Scope  |
| 现场发明权重                | 产生虚假精确感           | Evidence-first，评分延后 |
| 遇到缺数据用上一次值        | 制造假信号               | Health + fail closed     |
| 把美元 OI 变化当新增仓位    | 价格涨也会让名义 OI 涨   | open_interest 优先（基础资产数量）           |
| connected=true 当作数据正常 | WS 半死状态被忽略        | freshness watchdog       |
| 重连后重复累计 trade        | CVD 污染                 | trade_id dedup           |
| 为了过测试放宽阈值          | 掩盖实现错误             | 禁止改测试标准           |
| 前端复制一套计算            | 结果不一致               | UI 只消费 AnalysisEvent  |
| 大规模“顺手重构”            | 已验收模块失稳           | 文件范围 + ADR           |

# 20. 推荐开工顺序（第一批可直接喂给 AI 的任务）

| **顺序** | **Task**                                    | **完成后你能看到什么**      |
|----------|---------------------------------------------|-----------------------------|
| 01       | domain contracts + configs + test harness   | 仓库骨架和稳定事件模型      |
| 02       | aggTrade collector + reconnect + dedup      | 实时成交流，不做分析        |
| 03       | Data Health freshness watchdog              | 能识别“WS 连着但没数据”     |
| 04       | OI poller + time-aligned history            | 1m/5m OI 基础数量变化可信 |
| 05       | Taker Delta + windowed CVD                  | 真正的主动买卖资金流        |
| 06       | Volume/TradeCount/PriceAccel robust anomaly | 全市场异动候选              |
| 07       | Startup evidence model                      | SUSPECTED_START             |
| 08       | False-start veto pack                       | REJECTED vs START_CONFIRMED |
| 09       | Continuation / Withdrawal                   | 资金持续与撤离状态          |
| 10       | Replay + labels                             | 开始验证系统到底有没有 edge |
| 11       | Dashboard/Telegram                          | 最后再做漂亮展示            |

|     | **开工建议** 先让 AI 完成 01–05，并通过数据正确性 Gate；不要在第一天就做漂亮网页、AI 解读或“机会分”。 |
|-----|-------------------------------------------------------------------------------------------------------|

# 21. Definition of Done（整个 V1）

- 核心 P0 数据全天可用，能够识别 stale、断线、重复数据和 REST 限频。

- 相同 replay 输入得到相同 feature/state 输出。

- 每个 START_CONFIRMED / REJECTED / WITHDRAWAL 都能展开证据与反证。

- 系统至少积累一批可复盘的山寨币真/假启动样本，而不是只有 UI 截图。

- 未实现自动交易；LLM 不参与核心状态决定。

- 所有核心检测阈值集中配置，有测试、有版本记录，可逐步通过历史数据校准。

|     | **完成 V1 后再做什么** 拿真实样本回答“大佬说的 95% 假启动，到底哪些反证最有效”。这时再决定评分、权重、SMC/OB/FVG 等辅助层，而不是提前猜。 |
|-----|-------------------------------------------------------------------------------------------------------------------------------------------|
