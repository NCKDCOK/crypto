**资金行为驱动行情分析系统**

**系统设计规范（面向 AI Coding Agent）**

| **版本**     | v1.1                                                 |
|--------------|------------------------------------------------------|
| **日期**     | 2026-08-25                                           |
| **定位**     | 资金行为驱动的实时行情分析与启动雷达；不执行自动交易 |
| **开发模式** | AI Coding Agent Edition                              |

**核心原则：先证明“数据可信”和“资金行为可解释”，再谈评分与 AI 解读。**

# 0. 文档目的与设计结论

本文定义一个“资金行为驱动的实时行情分析系统”。系统只读取公开市场数据，识别资金异动、启动、延续、衰竭与撤离，并输出可解释证据、排行榜和提醒；它不是自动交易系统，不接用户账户、不下单、不管理仓位。

|     | **一句话产品定义** 全市场扫描“钱是否正在异常进入/退出”，先过滤假启动，再提醒用户关注真正有持续性的资金行为。 |
|-----|--------------------------------------------------------------------------------------------------------------|

- 核心研究对象不是 RSI/MACD，而是 Price × Volume × Trade Flow × Open Interest 的联动。

- “主力/庄家”仅作为用户界面的通俗表述；内部统一使用“推断的资金行为（inferred capital behavior）”，绝不声称识别到具体账户。

- V1 先输出 Evidence（证据链）和 State（状态），不使用拍脑袋的 0–100 机会分。

- AI 只负责解释和工程实现，不负责现场发明交易逻辑、权重或阈值。

- 数据不新鲜/不完整时 fail closed：输出 UNKNOWN / DATA_STALE，而不是继续生成漂亮信号。

# 1. 产品边界

| **属于本系统**                                   | **明确不属于本系统**              |
|--------------------------------------------------|-----------------------------------|
| Binance USDT-M 全市场扫描                        | 自动下单 / API Key / 账户资产管理 |
| 资金异动、启动、延续、撤离识别                   | 高杠杆策略执行与仓位管理          |
| OI、Funding、Premium、Taker Flow、CVD 等衍生分析 | “预测未来价格一定涨/跌”的黑盒模型 |
| 可解释证据、状态、候选列表、提醒                 | 未经验证的 AI 自由发挥评分        |
| 历史回放、样本标注、效果统计                     | 追求回测收益最大化的交易机器人    |

# 2. 核心设计哲学：跟随资金，不预测顶底

公开市场数据 → 资金留下的痕迹 → 行为推断 → 状态机  
  
异常（Anomaly） → 疑似启动（Suspected Start） → 确认启动（Confirmed）  
→ 延续（Continuation） → 衰竭（Exhaustion） → 撤离（Withdrawal）

系统需要回答的不是“这个币明天涨不涨”，而是以下三个问题：

1\. 现在有没有出现相对自身历史明显异常的资金活动？

2\. 这次异常更像新增资金推动，还是空头回补、去杠杆、单根脉冲等假启动？

3\. 已经启动后，资金是否仍在持续，什么时候出现衰竭或撤离证据？

# 3. 总体架构

┌──────────────── Binance Futures Public Data ────────────────┐  
│ aggTrade / Kline / Mark Price / OI / Funding / Premium ... │  
└──────────────────────┬──────────────────────────────────────┘  
↓  
\[Market Data Gateway\]  
↓  
\[Normalize + Dedup + Time Alignment\]  
↓  
\[Data Health\]  
↓ ↓  
HEALTHY STALE/DRIFT  
↓ └──→ 禁止生成确认信号  
\[Rolling Window Aggregator\]  
↓  
\[Feature Engine\]  
↓  
\[Anomaly / Startup / False-Start / Continuation / Withdrawal\]  
↓  
\[State Machine\]  
↓  
Evidence + State + Alert  
↓  
Dashboard / Telegram / Replay / Labeling

**事件分发拓扑（V1）：** 进程内 asyncio 单消费者 pipeline + 多订阅广播。

```
Collectors ──► Event Bus ──► Data Health（过滤/标注）
                              │
                              ├─► Feature Engine ──► Detectors ──► State Machine ──► AnalysisEvent Bus
                              │                                                          │
                              └─►（未来）其他消费者                                       ├─► API/WebSocket ──► UI
                                                                                        ├─► Alerts
                                                                                        └─► Storage/Replay
```

- 关键单向流：Collector → Health → Feature → Detector → StateMachine。Detector 不回写 Collector；State Machine 不回写 Feature。
- UI / Alerts / Storage 只订阅 AnalysisEvent，**禁止订阅原始行情自行计算**（否则结果不一致、复制业务逻辑）。
- V1 用进程内 asyncio.Queue；规模上升后迁 Redis Streams。

**Clock 抽象：** 所有时间相关逻辑（freshness watchdog、窗口边界、状态机 hold 计时）必须通过注入的 Clock 接口（`Clock.now_ms()` → UTC ms），禁止直接调用 `time.time()` / `datetime.now()`。
- `SystemClock`：wall time，生产用。
- `TestClock`：可控虚拟时间，deterministic replay 用（按 event_time 推进，保证可复现）。

|     | **重要分层** 数据采集、特征计算、行为检测、状态机、展示必须解耦。任何 Detector 都不能直接依赖 UI；任何 Collector 都不能包含“做多/做空”逻辑。 |
|-----|----------------------------------------------------------------------------------------------------------------------------------------------|

# 4. V1 数据源与优先级

| **优先级** | **数据**                | **主要用途**                         | **采集方式**   | **V1要求**             |
|------------|-------------------------|--------------------------------------|----------------|------------------------|
| P0         | aggTrade                | 真实成交、主动方向、Taker Delta、CVD | WebSocket      | 必须                   |
| P0         | 1m Kline                | 价格/成交量基线、收盘确认            | WebSocket      | 必须                   |
| P0         | Open Interest           | 新增/减少仓位、OI velocity           | REST 定时快照  | 必须                   |
| P0         | Mark Price + Premium    | 永续偏离、拥挤上下文                 | WebSocket/REST | 必须                   |
| P0         | Funding Rate            | 拥挤与持仓成本上下文                 | REST/WS        | 必须                   |
| P1         | Top Trader / Global L/S | 大户/全体方向差异                    | REST           | 推荐                   |
| P1         | Taker Buy/Sell Ratio    | 更慢周期主动方向确认                 | REST           | 推荐                   |
| P1         | Depth / BookTicker      | OFI、吸收、盘口失衡                  | WebSocket      | 第二阶段               |
| P2         | Liquidation             | 去杠杆/逼空辅助                      | WS/第三方      | 后续                   |
| P2         | Smart Signal 页面数据   | 大户成本背景                         | 非标准网页接口 | 仅背景，不参与实时确认 |

OI 变化必须优先使用合约数量/基础数量的变化；美元名义 OI 可用于展示，但不得把单纯的价格上涨误判为新增仓位。

# 5. 统一事件模型（Contracts）

AI 开发之前先锁定数据契约。所有模块只通过统一事件交互，禁止每个 Agent 自己定义一套 JSON。

## 5.0 全局枚举（先于所有对象定义）

**AggressorSide**（主动成交方向，从 Binance aggTrade 推导）：

| 值 | 含义 | Binance `m` 字段映射 |
|----|------|----------------------|
| `BUY` | 主动买入（taker 吃掉卖盘） | `m == false`（买方不是 maker → 买方是 taker） |
| `SELL` | 主动卖出（taker 吃掉买盘） | `m == true`（买方是 maker → 卖方是 taker） |
| `UNKNOWN` | 无法判定 | `m` 缺失或非法 |

> **P0 约束**：Binance aggTrade 的 `m` 表示**买方是否为 maker**。`m=true` ⇒ 卖方主动（SELL）。此映射若写反，CVD/Taker Delta 全量反转，任何实现必须对此写单元测试。

**Direction**：`LONG`（新增多头/向上推动）、`SHORT`（新增空头/向下推动）、`NEUTRAL`（方向不明，仅 anomaly 阶段可出现）、`null`（未推断）。

**ConfidenceState**（由 Data Health 派生，决定状态能否升级）：

| 值 | 含义 | 允许的最高状态 |
|----|------|----------------|
| `CONFIDENT` | 所有关键输入 OK | 全部（含 START_CONFIRMED / WITHDRAWAL） |
| `DEGRADED` | 存在 WARN，无 STALE/DRIFT/FAIL | 最高 SUSPECTED_START；禁止 CONFIRMED |
| `UNKNOWN` | 关键输入 STALE/DRIFT/FAIL | 禁止任何 CONFIRMED；已有 CONFIRMED 冻结 |

**HealthLevel**：`OK` / `WARN` / `STALE` / `DRIFT` / `FAIL`（定义见 §6）。

## 5.1 事件对象

所有时间戳为 **UTC 毫秒**（int64）。完整字段定义见 docs/DATA_MODEL.md。

```
TradeEvent {
  symbol, exchange, trade_id, event_time, receive_time,
  price, qty, quote_notional, aggressor_side, is_maker
}
```
- `is_maker` = Binance `m` 原值（买方是否为 maker），`aggressor_side` 由 `m` 派生（见 §5.0）。二者不冗余：一个是原始信号，一个是派生语义。
- `quote_notional = price × qty`（Binance 无独立字段，本地计算）。

```
KlineEvent {
  symbol, exchange, interval, open_time, close_time, event_time, receive_time,
  open, high, low, close, volume, quote_volume, trade_count, is_closed
}
```
- `is_closed`（Binance `x`）：仅 `is_closed=true` 的 bar 可进入慢周期确认。未闭合 bar 可多次更新，闭合后不可变。

```
OpenInterestSnapshot {
  symbol, exchange, event_time, receive_time,
  open_interest, source, freshness_ms
}
```
- `open_interest` = Binance `/fapi/v1/openInterest` 返回值，**单位 = 基础资产数量**（如 0.5 BTC），不是合约张数，也不是美元名义。
- `receive_time` 必须保留：OI health 用此算 age/freshness。
- **OI 变化判定**：`oi_change = open_interest_now − open_interest_asof`（基础资产数量差）。价格涨但 `open_interest` 不变 ⇒ `oi_change = 0`，不得把美元名义 OI 上涨误判为新增仓位。美元名义（`open_interest × mark_price`）仅展示用。

```
FundingRateSnapshot {
  symbol, exchange, event_time, receive_time,
  mark_price, index_price, last_funding_rate, next_funding_time, premium, source
}
```
- 仅作 context / soft veto，不单独触发信号。

```
HealthStatus {
  stream, symbol, status, last_event_time, last_receive_time, age_ms,
  stale_seconds, connected, subscribed, message_count, reconnect_count,
  sequence, reason
}
```
- 每个 stream 独立维护。`connected`（socket 是否 open）与 `healthy`（OK）必须分开判断——**connected ≠ healthy**。本表合并了原 stream 内部状态字段与契约字段。

```
FeatureSnapshot {
  symbol, asof, windows, data_health, features, provenance
}
```
- `features` 为 `map<string, FeatureValue>`，每个 FeatureValue 含 `value / available / window / baseline_ref`。
- `provenance` 记录每个特征的来源快照引用，不得只存最终分数。

```
AnalysisEvent {
  symbol, direction, previous_state, new_state,
  evidence[], vetoes[], asof, confidence_state
}
```

**Evidence 元素结构**：

| 字段 | 说明 |
|------|------|
| `family` | 证据族：`ANOMALY` / `FLOW` / `POSITION` / `PRICE_EFFECT` / `CONTEXT` |
| `type` | 如 `volume_z`、`taker_delta`、`oi_expansion` |
| `window` | 计算窗口（如 `30s`） |
| `value` | 原始特征值（缺数据为 null） |
| `reference` | baseline 参考值（如 median、MAD） |
| `threshold` | 触发阈值（来自配置） |
| `passed` | 是否满足该证据条件 |
| `source` | provenance 引用 |

**Veto 元素结构**：

| 字段 | 说明 |
|------|------|
| `type` | veto 类型（见 §9.3） |
| `triggered` | 是否命中否决 |
| `severity` | `hard`（禁止确认）/ `soft`（风险提示） |
| `detail` | 命中依据（如 retrace_ratio、delta_reversal 窗口） |

## 5.2 关键约束总表

| **对象**             | **必须字段**                                          | **关键约束**                                            |
|----------------------|-------------------------------------------------------|---------------------------------------------------------|
| TradeEvent           | trade_id / event_time / receive_time / aggressor_side | trade_id 必须去重；aggressor_side 由 Binance `m` 派生；exchange time 与本地接收时间同时保留 |
| KlineEvent           | interval / open_time / close_time / open/high/low/close/volume/trade_count/is_closed | 仅 is_closed=true 的 bar 可进入慢周期确认；闭合后不可变 |
| OpenInterestSnapshot | open_interest（基础资产数量）/ receive_time / freshness | 单位为基础资产数量，非美元名义；找历史基线时必须检查时间容差 |
| HealthStatus         | stream / status / last_receive_time / age_ms / connected / reason | connected 不等于 healthy；stream 内部状态全字段在此对象 |
| FeatureSnapshot      | 原始特征 + 窗口 + 数据来源 + provenance               | 不得只存最终分数，必须可追溯                              |
| AnalysisEvent        | state transition + evidence[] + veto[] + confidence_state | 任何状态变化必须能解释"为什么"；evidence/veto 须含结构化字段 |

# 6. Data Health：系统的第一优先级

|     | **Fail Closed** 任何关键输入处于 STALE / DRIFT / MISSING，Startup Confirm 和 Withdrawal Confirm 必须禁止或降级为 UNKNOWN。 |
|-----|----------------------------------------------------------------------------------------------------------------------------|

| **健康状态** | **含义**                       | **处理**                   |
|--------------|--------------------------------|----------------------------|
| OK           | 数据新鲜、顺序正常、无明显漂移 | 正常计算                   |
| WARN         | 部分非关键数据缺失或延迟升高   | 允许候选，不允许高置信确认 |
| STALE        | 超过该流的 freshness budget    | 禁止确认信号               |
| DRIFT        | 与备用源/交叉校验明显偏离      | 冻结相关特征               |
| FAIL         | 连接/解析/序列校验失败         | 重连并重建状态             |

- 每个 stream 独立维护：connected、subscribed、last_event_time、last_receive_time、message_count、reconnect_count、sequence、stale_seconds、age_ms（= now − last_receive_time）。

- aggTrade 重连后按 trade_id 去重，防止 CVD 被重复成交污染。

- OI 快照按时间对齐：找"5m 前"数据时必须在 `[target ± tolerance]` 内取最近一条，否则返回 `unavailable`，**不得回退取更旧数据**。默认容差 `15s`（配置化）。

- REST 必须集中 RateLimiter：读取 Binance 权重头、处理 429 Retry-After、指数退避与全局熔断，避免 418。

## 6.1 freshness budget（每流独立，默认值）

每个流超过 `freshness_budget` 即从 OK 降级 STALE。所有值配置化（configs/data_health.yaml），可按 symbol 覆盖。

| 流 | 默认 budget | 说明 |
|----|-------------|------|
| aggTrade（活跃 symbol） | 5s | 活跃币 5s 无成交即异常 |
| aggTrade（低活 symbol） | 30s | 冷门币放宽（按 24h 成交额分档） |
| 1m Kline | 90s | 每 60s 一根，90s 未收即 stale |
| OI poller | 2 × poll_interval | 默认 poll 5s，budget 10s |
| Funding/Premium | 60s | |

## 6.2 ConfidenceState 派生规则

关键流（V1）：`aggTrade`、`1m Kline`、`OI`。Funding/Premium 为非关键上下文流。

| 关键流状态 | ConfidenceState |
|------------|-----------------|
| 全部 OK | `CONFIDENT` |
| 存在 WARN，无 STALE/DRIFT/FAIL | `DEGRADED` |
| 任一关键流 STALE/DRIFT/FAIL | `UNKNOWN` |

ConfidenceState 对状态机的约束见 §10：`UNKNOWN` 禁止任何 CONFIRMED；`DEGRADED` 最高 SUSPECTED_START。

# 7. Rolling Window 与时间尺度

| **层级**   | **窗口建议**   | **职责**                                         |
|------------|----------------|--------------------------------------------------|
| 实时微观层 | 5s / 15s / 30s | 发现成交量、Taker Delta、CVD、价格加速度突然异常 |
| 启动层     | 1m / 3m / 5m   | 验证新增仓位、回踩承接、异常是否持续             |
| 趋势上下文 | 15m / 1h       | 判断更大方向、是否已经处于过度延伸阶段           |
| 背景层     | 4h / 24h       | Funding/OI regime、波动基线、市场环境            |

V1 的信号逻辑应优先使用"相对自身历史"的 robust baseline，例如 rolling median/MAD 或稳健 Z-score，避免固定阈值对不同山寨币失效。robust baseline 默认用过去 `1h` 的滚动数据计算 median/MAD（配置化，configs/features.yaml）；不同 symbol 不共享基线（"相对自身历史"）。

# 8. Feature Engine：V1 必做特征

| **类别** | **特征**                            | **解释**                                       |
|----------|-------------------------------------|------------------------------------------------|
| 量       | RVOL / Volume Z                     | 当前成交量相对近期正常水平的异常程度           |
| 量       | Trade Count Z                       | 成交笔数是否同步爆发，防止单笔大单制造假象     |
| 价       | Return / Price Acceleration         | 价格是否快速产生方向性位移                     |
| 资金流   | Taker Delta                         | 主动买入名义金额 - 主动卖出名义金额            |
| 资金流   | Windowed CVD / slope / acceleration | 主动成交方向是否持续并加速                     |
| 仓位     | OI Δ / velocity / acceleration      | 新增仓位还是平仓主导；使用 open_interest（基础资产数量，非美元名义）           |
| 效率     | Directional Efficiency              | 净位移 / 路径总位移，区分趋势与来回震荡        |
| 效率     | Flow Impact                         | 单位净主动资金推动的价格位移；极低可能表示吸收 |
| 结构     | Retrace Ratio / Acceptance          | 突破后回吐比例与在新区域停留时间               |
| 上下文   | Funding / Premium percentile        | 拥挤度，只做 context/soft veto，不做单独触发   |

DirectionalEfficiency = \|P_end - P_start\| / Σ\|ΔP_i\|  （窗口：30s / 1m）
  
FlowImpact = signed_return / max(\|net_taker_notional\|, ε)  （窗口：30s / 1m；ε 默认 1.0 USDT，配置化）
  
AbsorptionCandidate = \|Delta\| 很大 AND \|FlowImpact\| 很低  
（方向需要结合后续价格接受度、OI 与 OFI 再判断）

> 所有特征缺数据时返回 `null` / `unavailable`，不抛异常、不取旧值。每个特征须能回答：输入是什么、公式是什么、窗口是什么、缺数据时是什么、fixture 怎么复算。

# 9. 行为检测器设计

## 9.1 Anomaly Detector：只回答“有东西不正常”

- 输入：Volume Z、Trade Count Z、Price Accel Z、Taker Delta Z、OI velocity 等。

- 输出：AnomalyEvidence，不输出 LONG/SHORT，不代表“主力进场”。

- 目标：高召回，允许误报；把全市场缩小到候选集合。

## 9.2 Startup Detector：建立“新增资金推动”证据链

候选方向 + 量异常  
+ 主动成交同向  
+ OI 同向扩张（或可解释的 squeeze 例外）  
+ 价格产生有效位移  
+ 突破后未快速全部回吐  
─────────────────────────  
→ SUSPECTED_START / START_CONFIRMED

注意：Price↑ + Volume↑ 并不等于真启动。Price↑ + OI↓ + CVD↑ 更可能是空头回补；系统必须把“新增多头”和“逼空”分成不同 evidence type。

**squeeze 例外判定（§9.2 中的"可解释的 squeeze 例外"）：**

| 情形 | OI | 方向 | 判定 |
|------|----|------|------|
| 新增多头启动 | ↑ | LONG | 正常启动 |
| 新增空头启动 | ↑ | SHORT | 正常启动 |
| 空头回补（cover） | ↓ | LONG（价涨） | **squeeze 例外**：归类为 cover，非新增多头；evidence `oi_contraction_cover`，不进 START_CONFIRMED |
| 新空头被逼（短挤） | ↑ | LONG（价涨） | 标注 `squeeze_long`；需额外 CVD/接受度证据，降低 confidence |

> 判定依据：direction=LONG 且 OI 收缩 ⇒ cover 类（非新增资金）。direction=LONG 且 OI 扩张但 Funding 极度负（空头拥挤）⇒ 标注 squeeze 风险。具体阈值配置化。


## 9.3 False Start Filter：V1 的核心 Edge

| **VetoType** | **severity** | **典型表现** | **处理** |
|---------------|--------------|--------------|----------|
| `data_stale` | hard | 关键输入过期 | 禁止确认 |
| `rapid_retrace` | hard | 异常上涨后短时间回吐大部分位移 | 拒绝或降级候选 |
| `oi_contraction` | hard | 上涨但 OI 持续下降（非 squeeze 例外） | 分类为 squeeze/cover，不作为新增多头启动 |
| `delta_reversal` | hard | 第一波主动买入后迅速翻为持续主动卖出 | 拒绝 |
| `no_acceptance` | hard | 突破后无法在新价格区域停留 | 拒绝 |
| `low_efficiency_absorption` | soft | 巨量成交但价格推不动 | 标记吸收风险，等待二次确认 |
| `crowding_extreme` | soft | Funding/Premium 极端且追涨 | 风险提示 |

|     | **禁止事项** V1 不允许 AI 自行写 “score = 0.2\*RSI + 0.4\*Volume + ...”。所有 veto 与 evidence 必须先有定义、fixture 和历史样本，再决定权重。 |
|-----|-----------------------------------------------------------------------------------------------------------------------------------------------|

## 9.4 Continuation Detector：主力还在不在

- OI 是否继续扩张或至少保持，而不是启动后立即坍缩。

- CVD / Taker Delta 是否维持方向，回踩时反向主动成交是否明显减弱。

- 成交量是否从“爆发”过渡到“健康延续”，而非每一根都需要极端放量。

- 价格效率是否维持，是否出现大额资金却越来越推不动。

- 二次突破是否获得接受（acceptance），而不是刺穿后立即回落。

## 9.5 Withdrawal Detector：资金撤离模型独立设计

撤离不是“启动条件消失”。重点观察：  
  
Price 继续创新高  
BUT CVD 不创新高 / Delta 转弱  
+ OI 从扩张 → 走平 → 收缩  
+ Volume 仍大但 FlowImpact 越来越低  
+ 主动卖出持续增强  
─────────────────────────────  
→ EXHAUSTION → WITHDRAWAL

WithdrawalDetector 的输出应首先是“资金撤离风险/确认”，而不是 SELL 指令。用户可自行决定是否止盈、移动止损或退出。

# 10. 状态机模型

```
                     ┌── anomaly 消退 ──┐
                     ▼                  │
                  SLEEPING ── anomaly ──► ANOMALY
                     ▲                     │
                     │                     │ direction evidence
                     │                     ▼
                     │              SUSPECTED_START
                     │                │       │
                     │     veto hit   │       │ confirmed + confidence≠UNKNOWN
                     │        └────────┘       ▼
                     │              START_CONFIRMED
                     │                     │
            cooldown │                     │ sustained flow
                     ▼                     ▼
                  COOLDOWN ◄─ rejected  CONTINUATION
                     ▲                     │ │
                     │              efficiency│weakening/divergence
                     │              recover  ▼ │
                     │            ◄──────── EXHAUSTION
                     │                     │
            cooldown │                     │ withdrawal confirmed
                     ▼                     ▼
                  COOLDOWN ◄────────── WITHDRAWAL
```

> REJECTED 为瞬时记录状态，进入后立即转 COOLDOWN（不驻留）。EXHAUSTION→CONTINUATION 允许回退（需新证据）。完整转移 guard 见 §10.1。

| **状态**          | **用户含义**                   | **是否提醒**       |
|-------------------|--------------------------------|--------------------|
| SLEEPING          | 无明显异常                     | 否                 |
| ANOMALY           | 资金/量价出现异常              | 可选低等级提醒     |
| SUSPECTED_START   | 疑似启动，尚未通过反证过滤     | 默认不推“进场”提醒 |
| START_CONFIRMED   | 证据链通过，假启动 veto 未命中 | 高等级提醒         |
| CONTINUATION      | 资金仍在持续                   | 状态更新/排行榜    |
| EXHAUSTION        | 推动效率下降、背离增加         | 风险提醒           |
| WITHDRAWAL        | 撤离证据达到确认条件           | 高等级撤离提醒     |
| REJECTED/COOLDOWN | 假启动或信号失效               | 记录并用于后续标注 |

## 10.1 完整转移 guard 表

> 所有阈值来自配置（configs/state_machine.yaml），AI 不得自行调参。证据族定义见 §5/§9。`confidence_state` 定义见 §5.0/§6.2。

| # | from | to | guard（全部满足） |
|---|------|----|-------------------|
| T1 | SLEEPING | ANOMALY | 任一 anomaly 证据 passed（VolumeZ/TradeCountZ/PriceAccelZ/TakerDeltaZ 超阈值）；confidence_state ∈ {CONFIDENT, DEGRADED} |
| T2 | ANOMALY | SLEEPING | anomaly 窗口内无新增证据 passed 且持续 anomaly_decay_window 无复发 |
| T3 | ANOMALY | SUSPECTED_START | 方向证据 direction ∈ {LONG, SHORT}：主动资金同向 + 价格有效位移 +（OI 同向扩张 或 squeeze 例外）；confidence_state ∈ {CONFIDENT, DEGRADED} |
| T4 | SUSPECTED_START | REJECTED | 任一 hard veto triggered（data_stale / rapid_retrace / oi_contraction〔非 squeeze 例外〕 / delta_reversal / no_acceptance） |
| T5 | SUSPECTED_START | START_CONFIRMED | 启动证据族 A–D 全 passed；无 hard veto 命中；confidence_state == CONFIDENT；满足 confirmation_hold（证据持续 ≥ 配置时间，防单次 spike 直接确认） |
| T6 | START_CONFIRMED | CONTINUATION | OI 维持/扩张 + CVD 维持同向 + 回踩反向主动成交减弱 + 效率健康；持续 continuation_hold |
| T7 | CONTINUATION | EXHAUSTION | 价格创新高但 CVD 不创新高/转弱 或 OI 走平→收缩 或 FlowImpact 持续下降；满足 exhaustion_confirm 窗口 |
| T8 | EXHAUSTION | WITHDRAWAL | OI 收缩 + delta/CVD 反转 + 主动卖出持续增强 + 价格结构失守；confidence_state == CONFIDENT；满足 withdrawal_confirm 窗口 |
| T9 | EXHAUSTION | CONTINUATION | 推动效率恢复（背离消失、OI 重新扩张）— 允许回退，需新证据 |
| T10 | WITHDRAWAL | COOLDOWN | 自动（撤离确认后进入冷却） |
| T11 | REJECTED | COOLDOWN | 自动（瞬时，不驻留） |
| T12 | COOLDOWN | SLEEPING | cooldown_seconds 到期 |

## 10.2 配置项（configs/state_machine.yaml）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `anomaly_decay_window` | 30s | anomaly 无复发后退回 SLEEPING 的窗口 |
| `confirmation_hold` | 15s | SUSPECTED→CONFIRMED 证据需持续的最低时间 |
| `continuation_hold` | 30s | CONFIRMED→CONTINUATION 证据需持续 |
| `exhaustion_confirm` | 1m | 衰竭证据需持续的窗口 |
| `withdrawal_confirm` | 1m | 撤离证据需持续的窗口 |
| `cooldown_seconds` | 300 | REJECTED/WITHDRAWAL 后冷却秒数 |

## 10.3 Data Health 对状态机的硬约束（Fail Closed）

| confidence_state | 允许进入的最高状态 |
|------------------|--------------------|
| CONFIDENT | 全部（含 START_CONFIRMED / WITHDRAWAL） |
| DEGRADED | 最高 SUSPECTED_START；已 CONFIRMED 的冻结不退出 |
| UNKNOWN | 禁止任何 CONFIRMED；ANOMALY 可保持但标注 data_stale；已有 CONFIRMED 冻结 |

> 每次状态转移必须产出 AnalysisEvent（含 evidence + vetoes），无证据的状态变化非法。状态转移必须确定性：相同 FeatureSnapshot 序列 + 相同配置 ⇒ 相同状态序列。REJECTED/WITHDRAWAL 必须经 COOLDOWN 回 SLEEPING，不得直接跳到 START_CONFIRMED。

# 11. Evidence-first 输出模型

ONGUSDT  
STATE = SUSPECTED_START  
DIRECTION = LONG  
  
Evidence:  
VolumeZ(30s) +4.72 ✓  
TradeCountZ +3.81 ✓  
PriceAccelZ +2.94 ✓  
OI_1m +1.82% ✓  
TakerDelta +1.92M ✓  
CVD_slope_z +3.12 ✓  
FlowImpact healthy ✓  
  
Veto:  
rapid_retrace false  
oi_contraction false  
delta_reversal false  
data_stale false  
  
结论：疑似新增多头资金启动，等待 acceptance / 二次确认。

只有在积累足够历史样本后，才进入 v1.5/v2 的 calibrated score：由命中率、条件发生频率和不同币种/市场 regime 的后验表现确定，而不是人为“给 20 分”。

# 12. 历史回放与标注：系统学习的基础

- 保存原始事件的必要子集、FeatureSnapshot、状态转换和所有 evidence/veto。

- 支持按事件时间 Replay，结果必须可重复（deterministic）。

- 对每个 SUSPECTED_START / START_CONFIRMED 保存后续 1m/5m/15m/1h 最大有利/不利变动、回撤、持续时间。

- 标注 false start、clean continuation、squeeze-only、absorption、withdrawal 等类别。

- 未来若做评分/概率模型，只允许使用这些历史标签校准。

# 13. 推荐技术栈

| **层**    | **推荐**                                       | **理由**                                       |
|-----------|------------------------------------------------|------------------------------------------------|
| 后端      | Python 3.12 + asyncio + FastAPI + Pydantic     | AI 生成/审查成本低，生态完整，适合事件驱动分析 |
| 缓存/队列 | 进程内 asyncio.Queue；规模上升后 Redis Streams | V1 保持简单，先避免分布式复杂度                |
| 数据库    | PostgreSQL；时间序列量大后 TimescaleDB         | 保存快照、状态、事件索引与回放元数据           |
| 前端      | React + TypeScript + Vite                      | 做市场雷达、证据详情和数据健康面板             |
| 测试      | pytest + pytest-asyncio + hypothesis           | 单元/异步/性质测试                             |
| 部署      | Docker Compose + systemd/pm2 等进程管理        | 本地与服务器环境一致                           |
| 可观测性  | Prometheus-style metrics + structured logging  | 监控 stale、重连、队列积压、429/418 风险       |

## 13.1 必须 metrics 清单

| metric | 含义 |
|--------|------|
| `stream_age_ms{stream,symbol}` | 各流 age |
| `stream_status{stream,symbol}` | health level |
| `reconnect_count{stream}` | 重连次数 |
| `queue_depth{stage}` | 各 stage 队列积压 |
| `rest_weight_used` | REST 权重消耗 |
| `rest_429_total` / `rest_418_total` | 限频/封禁计数 |
| `state_transitions_total{from,to}` | 状态转移计数 |
| `feature_compute_lag_ms` | 特征计算延迟 |
| `feature_available_ratio{feature}` | 特征可用率 |


# 14. 代码目录建议（供 AI Agent 固化边界）

src/  
domain/ \# 统一事件、枚举、状态机 contracts  
collectors/ \# Binance WS / REST；只采集与标准化  
health/ \# freshness / sequence / drift / rate-limit  
windows/ \# rolling buffers / time alignment  
features/ \# volume / delta / CVD / OI / efficiency ...  
detectors/ \# anomaly / startup / false_start / continuation / withdrawal  
state_machine/ \# 状态转换与 transition guards  
storage/ \# repository abstraction  
api/ \# FastAPI endpoints / websocket to UI  
alerts/ \# Telegram 等，仅消费 AnalysisEvent  
replay/ \# deterministic replay / labeling  
observability/ \# metrics / structured logs  
clock/ \# clock abstraction（SystemClock / TestClock，deterministic replay 基础）  
config/ \# 配置加载与 Pydantic schema 校验  
  
configs/  
fixtures/  
tests/  
docs/  
specs/

# 15. 开源项目复用边界

| **项目**                                | **许可**      | **适合直接复用/借鉴**                                      | **不要照搬**                               |
|-----------------------------------------|---------------|------------------------------------------------------------|--------------------------------------------|
| 0xBennie/binance-smart-money-oi-monitor | MIT           | OI 基础数量变化、时间对齐思想、418/429 防护、历史快照    | Smart Signal 网页接口作为实时启动核心      |
| Co-Messi/HyperData-Terminal             | Apache-2.0    | Data Health、stale/drift、aggTrade CVD、去重、公开数据架构 | LLM 买卖判断、与本项目无关的 paper trading |
| SoCloseSociety/TradeBobbyTerminal       | MIT           | 订单流 UI、closed-bar 确认、Funding/OI/CVD 展示            | 宏观/SMC 大而全模块整体移植                |
| Mattbusel/fin-stream                    | MIT           | OFI、VPIN、orderbook sequence、rolling microstructure 思想 | 为性能而引入 Rust 作为 V1 主栈             |
| tiagosiebler/orderflow                  | MIT；维护较旧 | Footprint / stacked imbalance 算法参考                     | 直接作为长期核心依赖                       |

实际复制代码前必须再次核查仓库当前 LICENSE、NOTICE 与依赖许可证；“参考思想”与“复制源代码”是两个不同动作。

# 16. 安全、合规与措辞

- 默认只使用公开市场数据，不要求用户 Binance API Key。

- UI 中可用“主力资金/资金进入”作为简化语言，但详情页必须解释为统计推断，不是识别具体账户。

- 所有提醒明确为行情分析信号，不保证收益，不自动执行交易。

- LLM 无权改变 detector 的状态、阈值或证据；只允许读取结构化 AnalysisEvent 生成自然语言说明。

# 17. V1 验收标准

| **领域** | **验收门槛**                                                                       |
|----------|------------------------------------------------------------------------------------|
| 数据     | 核心 P0 数据连续运行；断线可重连；重复 trade 不污染；STALE 可被正确识别            |
| 限频     | REST 有集中 RateLimiter、429 Retry-After、熔断；不会因每个模块各自轮询造成请求风暴 |
| 特征     | CVD/OI/Volume/Delta/efficiency 可用固定 fixture 复算并得到一致结果                 |
| 检测     | 每一次状态转换都有 evidence 与 veto；不存在无法解释的“87 分”                       |
| 回放     | 相同输入事件重放两次，状态序列与特征输出一致                                       |
| UI       | 排行榜可看到 state、direction、data health；详情页可展开完整证据链                 |
| 范围     | 无自动下单、仓位管理、用户 API Key 逻辑                                            |

# 18. V1 之后的演进顺序

1\. P1：加入 order book / OFI、吸收（absorption）与 stacked imbalance。

2\. P1：加入 Top Trader / Global L/S、Taker Ratio 作为背景/确认，不当作单独主信号。

3\. P1：建立事件标签数据集，统计不同 evidence 在不同币种和 regime 下的后验效果。

4\. v1.5：在历史样本基础上生成 calibrated probability / opportunity score，并展示置信区间。

5\. v2：多交易所交叉验证、清算流、跨市场 CVD 与更复杂的资金撤离模型。

6\. 最后才考虑 RSI/MACD/OB/FVG 等辅助结构模块，避免反客为主。

|     | **最终原则** 项目的价值不在“指标数量”，而在“能否用可信数据证明资金行为发生了什么，并且能用历史样本验证假启动过滤是否真的有效”。 |
|-----|---------------------------------------------------------------------------------------------------------------------------------|
