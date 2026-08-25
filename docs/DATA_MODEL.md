# 数据模型契约（Data Model）

| 版本 | v1.0 |
|------|------|
| 依据 | SYSTEM_DESIGN.md §5 |
| 状态 | 已锁定 contract（Gate 0 后变更需 ADR） |

本文是 SYSTEM_DESIGN.md §5 的**权威细化**。所有模块只能通过本文定义的事件交互，禁止各 Agent 自定义 JSON。所有时间戳为 **UTC 毫秒**（int64），除非另行说明。

---

## 0. 全局枚举（先于所有对象定义）

### 0.1 AggressorSide

主动成交方向。从 Binance aggTrade 推导。

| 值 | 含义 | Binance `m` 字段映射 |
|----|------|----------------------|
| `BUY` | 主动买入（taker 吃掉卖盘） | `m == false`（买方不是 maker → 买方是 taker） |
| `SELL` | 主动卖出（taker 吃掉买盘） | `m == true`（买方是 maker → 卖方是 taker） |
| `UNKNOWN` | 无法判定（异常/补齐数据） | 缺失或非法 `m` |

> **P0 约束**：Binance aggTrade 的 `m` 表示**买方是否为 maker**。`m=true` ⇒ 卖方主动（SELL）。此映射若写反，CVD/Taker Delta 全量反转。任何实现必须对此映射写单元测试。

### 0.2 Direction

| 值 | 含义 |
|----|------|
| `LONG` | 证据指向新增多头 / 向上推动 |
| `SHORT` | 证据指向新增空头 / 向下推动 |
| `NEUTRAL` | 方向不明或矛盾，仅 anomaly 阶段可出现 |
| `null` | 未推断（如纯 anomaly 无方向） |

### 0.3 ConfidenceState

状态机的置信上下文，直接由 Data Health 派生，决定状态能否升级。

| 值 | 含义 | 允许的状态升级 |
|----|------|----------------|
| `CONFIDENT` | 所有关键输入 OK | 允许 → START_CONFIRMED / WITHDRAWAL |
| `DEGRADED` | 存在 WARN（非关键缺失/延迟升高） | 允许 → SUSPECTED_START，禁止 → CONFIRMED |
| `UNKNOWN` | 关键输入 STALE/DRIFT/MISSING | 禁止任何 CONFIRMED，已有 CONFIRMED 冻结 |

> ConfidenceState 不是人为打分，是 HealthStatus 的函数（见 DATA_HEALTH.md §3）。

### 0.4 HealthLevel

| 值 | 含义 |
|----|------|
| `OK` | 数据新鲜、顺序正常、无明显漂移 |
| `WARN` | 部分非关键数据缺失或延迟升高 |
| `STALE` | 超过该流的 freshness budget |
| `DRIFT` | 与备用源/交叉校验明显偏离 |
| `FAIL` | 连接/解析/序列校验失败 |

### 0.5 KlineInterval

仅 `1m` 在 V1 作为慢周期确认源。其余用于上下文。

`1m`, `3m`, `5m`, `15m`, `1h`, `4h`, `1d`

---

## 1. TradeEvent

逐笔成交。来源：Binance aggTrade WebSocket（`<symbol>@aggTrade`）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | 是 | 如 `BTCUSDT` |
| `exchange` | string | 是 | V1 固定 `"binance"`；预留多交易所 |
| `trade_id` | int64 | 是 | Binance `a`；**去重主键**，重连后据此丢弃重复 |
| `event_time` | int64(ms) | 是 | Binance `T`（交易所事件时间，UTC） |
| `receive_time` | int64(ms) | 是 | 本地接收时间（UTC）；与 event_time 同时保留用于 lag/health |
| `price` | decimal(str) | 是 | Binance `p`，保留字符串精度 |
| `qty` | decimal(str) | 是 | Binance `q`，基础资产数量 |
| `quote_notional` | decimal(str) | 是 | `price × qty`；Binance 字段无独立 quoteQty，须本地计算 |
| `aggressor_side` | AggressorSide | 是 | 由 `m` 推导（见 §0.1） |
| `is_maker` | bool | 是 | **Binance `m` 原值**（买方是否为 maker）。保留原始字段，不与 aggressor_side 冗余——`is_maker` 是原始信号，`aggressor_side` 是派生语义 |

**不变量**：`trade_id` 在同一 symbol 内严格递增；重连后 `trade_id <= 已见最大 trade_id` 的记录必须丢弃。

---

## 2. KlineEvent

K 线。来源：Binance Kline WebSocket（`<symbol>@kline_1m`）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | 是 | |
| `exchange` | string | 是 | `"binance"` |
| `interval` | KlineInterval | 是 | V1 确认源仅 `1m` |
| `open_time` | int64(ms) | 是 | Binance `t`（K 线开盘时间） |
| `close_time` | int64(ms) | 是 | Binance `T`（K 线收盘时间） |
| `event_time` | int64(ms) | 是 | Binance `E`（事件推送时间） |
| `receive_time` | int64(ms) | 是 | 本地接收时间 |
| `open` | decimal(str) | 是 | `o` |
| `high` | decimal(str) | 是 | `h` |
| `low` | decimal(str) | 是 | `l` |
| `close` | decimal(str) | 是 | `c` |
| `volume` | decimal(str) | 是 | `v`，基础资产成交量 |
| `quote_volume` | decimal(str) | 否 | `q`，USDT 名义成交额 |
| `trade_count` | int | 是 | `n`；用于 TradeCountZ |
| `is_closed` | bool | 是 | Binance `x`；**仅 `is_closed=true` 的 bar 可进入慢周期确认** |

**不变量**：同一 `(symbol, interval, open_time)` 的未闭合 bar 可被多次更新（覆盖）；闭合后不可变。

---

## 3. OpenInterestSnapshot

持仓量快照。来源：Binance REST `GET /fapi/v1/openInterest`（定时轮询）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | 是 | |
| `exchange` | string | 是 | `"binance"` |
| `event_time` | int64(ms) | 是 | 快照对应的市场时间（REST 响应无显式时间时用 receive_time） |
| `receive_time` | int64(ms) | 是 | 本地接收时间；**OI health 用此算 age** |
| `open_interest` | decimal(str) | 是 | Binance 返回值，**单位 = 基础资产数量**（如 `0.5` = 0.5 BTC） |
| `source` | string | 是 | `"binance_rest_openinterest"` |
| `freshness_ms` | int | 是 | 该快照距今年龄 = now - receive_time；序列化时计算 |

> **P0 约束 — OI 单位**：Binance `/fapi/v1/openInterest` 返回的 `openInterest` 是**基础资产数量**，不是合约张数，也不是美元名义。本文用字段名 `open_interest` 承载此值。
>
> **OI 变化判定**：`oi_change = open_interest_now − open_interest_asof`（基础资产数量差）。价格涨但 `open_interest` 不变 ⇒ `oi_change = 0`，**不得**把美元名义 OI 的上涨误判为新增仓位。若需展示美元名义，另算 `open_interest × mark_price`，仅展示用，不参与检测。

**as-of lookup**：查找 N 分钟前快照时，必须在 `[target − tolerance, target + tolerance]` 内取最近一条；无满足条件的快照则返回 `unavailable`（见 DATA_HEALTH.md）。默认 `tolerance` 配置化。

---

## 4. FundingRateSnapshot

资金费率。来源：Binance REST `GET /fapi/v1/premiumIndex`（含 mark price / premium）或 `/fapi/v1/fundingRate`。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | 是 | |
| `exchange` | string | 是 | `"binance"` |
| `event_time` | int64(ms) | 是 | |
| `receive_time` | int64(ms) | 是 | |
| `mark_price` | decimal(str) | 是 | 标记价格 |
| `index_price` | decimal(str) | 是 | 指数价格 |
| `last_funding_rate` | decimal(str) | 是 | 当前资金费率（小数，如 `0.0001` = 0.01%） |
| `next_funding_time` | int64(ms) | 是 | 下次结算时间 |
| `premium` | decimal(str) | 是 | `mark_price − index_price`；可正可负 |
| `source` | string | 是 | `"binance_rest_premiumindex"` |

**用途**：仅作 context / soft veto（拥挤度），**不单独触发信号**。

---

## 5. HealthStatus

单个数据流的健康状态。每个 stream 独立维护并周期性发出。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stream` | string | 是 | 流标识，如 `aggTrade:BTCUSDT` / `oi_poller:BTCUSDT` |
| `symbol` | string | 否 | 关联 symbol（全局流可为空） |
| `status` | HealthLevel | 是 | OK/WARN/STALE/DRIFT/FAIL |
| `last_event_time` | int64(ms) | 否 | 最近一条有效事件时间（无则 null） |
| `last_receive_time` | int64(ms) | 否 | 最近接收时间 |
| `age_ms` | int | 否 | `now − last_receive_time`（无事件则 null） |
| `stale_seconds` | int | 否 | 已 stale 的秒数（仅 STALE/FAIL 有意义） |
| `connected` | bool | 是 | socket/连接是否 open。**connected ≠ healthy** |
| `subscribed` | bool | 否 | 是否成功订阅 |
| `message_count` | int64 | 是 | 累计消息数 |
| `reconnect_count` | int | 是 | 重连次数 |
| `sequence` | int64 | 否 | 序列号（仅 depth 类有；aggTrade 无序列，靠 trade_id） |
| `reason` | string | 否 | 非 OK 时的原因码 |

> 本表合并了 SYSTEM_DESIGN §6 文字描述的 stream 内部状态与 §5 契约的 5 字段，二者为同一对象。

---

## 6. FeatureSnapshot

特征引擎输出。一个 symbol 在某时刻全部窗口特征的快照。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | 是 | |
| `asof` | int64(ms) | 是 | 快照生成时间 |
| `windows` | object | 是 | 窗口尺寸集合，如 `{"micro": "5s", "startup": "1m", ...}` |
| `data_health` | object | 是 | 该 symbol 各关键流的 HealthStatus 摘要（stream → status） |
| `features` | map<string, FeatureValue> | 是 | 特征名 → 值。见 ANALYSIS_MODEL.md §2 |
| `provenance` | object | 是 | 每个特征的来源快照引用，如 `{"rvol_30s": {"trade_count": 1234, "window_start": ..., "source_streams": [...]}}` |

### FeatureValue

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `value` | number \| null | 是 | 计算值；缺数据时 `null` |
| `available` | bool | 是 | 是否可用（`value==null` 时 false） |
| `window` | string | 否 | 计算窗口 |
| `baseline_ref` | object | 否 | robust baseline 参考值（median/MAD、样本数） |

**不变量**：FeatureSnapshot 必须可追溯到原始事件（provenance）；不得只存最终分数。

---

## 7. AnalysisEvent

检测器 + 状态机输出。每次状态变化产生一条。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | 是 | |
| `direction` | Direction | 是 | LONG/SHORT/NEUTRAL/null |
| `previous_state` | State | 是 | 见 STATE_MACHINE.md |
| `new_state` | State | 是 | |
| `evidence` | Evidence[] | 是 | 支持本次转移的证据（可为空数组） |
| `vetoes` | Veto[] | 是 | 本次评估的否决项（命中或未命中均记录） |
| `asof` | int64(ms) | 是 | |
| `confidence_state` | ConfidenceState | 是 | 派生自 data_health |

### Evidence

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `family` | EvidenceFamily | 是 | `ANOMALY`/`FLOW`/`POSITION`/`PRICE_EFFECT`/`CONTEXT` |
| `type` | string | 是 | 如 `volume_z`、`taker_delta`、`oi_expansion` |
| `window` | string | 否 | `30s`/`1m` 等 |
| `value` | number \| null | 是 | 原始特征值 |
| `reference` | object | 否 | baseline 参考值（如 median、z 分母） |
| `threshold` | number \| null | 否 | 触发阈值（来自配置） |
| `passed` | bool | 是 | 是否满足该证据条件 |
| `source` | string | 否 | provenance 引用 |

### Veto

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | VetoType | 是 | 见 ANALYSIS_MODEL.md §4 |
| `triggered` | bool | 是 | 是否命中否决 |
| `severity` | `hard` \| `soft` | 是 | hard=禁止确认；soft=风险提示 |
| `detail` | object | 否 | 命中依据（如 retrace_ratio、delta_reversal 窗口） |

**不变量**：任何状态变化必须能解释"为什么"——`evidence` 与 `vetoes` 不得同时为空且无状态语义。LLM 只读 AnalysisEvent 生成自然语言，不得覆盖 new_state。
