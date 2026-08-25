# 数据健康模型（Data Health）

| 版本 | v1.0 |
|------|------|
| 依据 | SYSTEM_DESIGN.md §6, Gate 2 |
| 状态 | 已锁定 contract |

**第一优先级原则：Fail Closed。** 任何关键输入处于 STALE/DRIFT/MISSING 时，Startup Confirm 与 Withdrawal Confirm 必须禁止或降级为 UNKNOWN。

---

## 0. 核心区分：connected ≠ healthy

| 概念 | 含义 | 误用风险 |
|------|------|----------|
| `connected` | socket 是否 open | 把"连着但没数据"当成健康 → 造假信号 |
| `healthy` (OK) | 数据新鲜、顺序正常、无漂移 | 唯一允许产生确认信号的状态 |

WS socket 可能 open 但长时间无推送（半死状态）。**必须用 freshness watchdog 独立判断 healthy，不能只看 connected。**

---

## 1. HealthLevel 定义

见 DATA_MODEL.md §0.4。

| 状态 | 含义 | 处理 |
|------|------|------|
| OK | 数据新鲜、顺序正常、无明显漂移 | 正常计算 |
| WARN | 部分非关键数据缺失或延迟升高 | 允许候选，不允许高置信确认 |
| STALE | 超过该流的 freshness budget | 禁止确认信号 |
| DRIFT | 与备用源/交叉校验明显偏离 | 冻结相关特征 |
| FAIL | 连接/解析/序列校验失败 | 重连并重建状态 |

---

## 2. 每个 stream 维护的运行时状态

合并自 SYSTEM_DESIGN §6（与 HealthStatus 契约同一对象，见 DATA_MODEL.md §5）：

- `connected` / `subscribed`
- `last_event_time` / `last_receive_time`
- `age_ms` = now − last_receive_time
- `message_count` / `reconnect_count`
- `sequence`（仅 depth 类；aggTrade 无序列，靠 trade_id）
- `stale_seconds`

---

## 3. freshness budget（每流独立）

每个流的 `freshness_budget_ms`：超过即从 OK 降级为 STALE。

| 流 | 默认 budget | 触发事件类型 | 说明 |
|----|-------------|--------------|------|
| aggTrade（活跃 symbol） | `5_000` (5s) | TradeEvent | 活跃币 5s 无成交即异常 |
| aggTrade（低活 symbol） | `30_000` (30s) | TradeEvent | 冷门币放宽（按 symbol 成交活跃度分级） |
| 1m Kline | `90_000` (90s) | KlineEvent(is_closed) | 每 60s 一根，90s 未收即 stale |
| OI poller | `2 × poll_interval` | OpenInterestSnapshot | 默认 poll 5s，budget 10s |
| Funding/Premium | `60_000` | FundingRateSnapshot | |

> 配置路径 `configs/data_health.yaml`。budget 可按 symbol 覆盖。

**分级策略**：symbol 按 24h 成交额分档（active / normal / low），aggTrade freshness budget 随档调整，避免冷门币误报 stale。

---

## 4. ConfidenceState 派生规则

ConfidenceState（见 DATA_MODEL.md §0.3）是该 symbol **关键流** HealthLevel 的函数，由 Data Health 模块统一计算并写入 FeatureSnapshot/AnalysisEvent。

关键流定义（V1）：`aggTrade`、`1m Kline`、`OI`。Funding/Premium 为非关键上下文流。

| 关键流状态 | ConfidenceState |
|------------|-----------------|
| 全部 OK | `CONFIDENT` |
| 存在 WARN，无 STALE/DRIFT/FAIL | `DEGRADED` |
| 任一关键流 STALE/DRIFT/FAIL | `UNKNOWN` |

对状态机的约束见 STATE_MACHINE.md §4。

---

## 5. 必测场景（Gate 2 验收）

| 场景 | 预期 |
|------|------|
| WS socket open，但 30s 无 aggTrade | 对应 stream → STALE |
| 重连后重复一笔 trade（同 trade_id） | 丢弃重复，CVD 前置数据不双计 |
| 5m 前没有足够接近的 OI 快照 | `oi_change_5m = unavailable`，**而非**取 9m 前数据 |
| 429 返回 Retry-After | 全局限流按策略退避；不让其他模块各自重试 |
| 关键数据 STALE | 后续 detector 无权进入 CONFIRMED（confidence_state=UNKNOWN） |
| WS 半死（TCP open 但无心跳/数据） | freshness watchdog 在 budget 内降级 STALE |

---

## 6. 去重与序列

| 流 | 去重键 | 序列校验 |
|----|--------|----------|
| aggTrade | `trade_id`（同 symbol 严格递增，≤已见最大值则丢弃） | 无（Binance aggTrade 无序列字段） |
| Kline | `(symbol, interval, open_time)` + `is_closed` | 闭合 bar 不可变 |
| OI poller | `(symbol, receive_time)` 快照 | 无序列；按时间对齐 |
| Depth（P1，未来） | lastUpdateId | 必须校验 `U..u` 序列连续，缺口需 resync |

> aggTrade 重连后按 trade_id 去重，**防止 CVD 被重复成交污染**。这是 P0 级不变量。

---

## 7. OI as-of lookup 与时间容差

查找 N 分钟前 OI 快照时：

1. `target_time = now − N`
2. 在 `[target_time − tolerance, target_time + tolerance]` 内取 `receive_time` 最近的快照
3. 无满足条件的快照 → 返回 `unavailable`（**不得**取窗口外数据充数）

| 配置 | 默认值 |
|------|--------|
| `oi_lookup_tolerance` | `15_000` (15s) |

多档查询（1m/5m/15m）共用同一容差配置。

---

## 8. REST 集中限频（RateLimiter）

REST 请求必须集中到单一 RateLimiter，禁止各模块各自轮询。

- 读取 Binance 响应头 `X-MBX-USED-WEIGHT-*`，维护全局权重预算。
- 收到 429：读取 `Retry-After`，全局限流退避。
- 指数退避 + 全局熔断，避免触发 418（IP 封禁）。
- OI poller 错误不阻塞其他 symbol（per-symbol 隔离失败）。

| 配置 | 默认值 |
|------|--------|
| `rest_weight_limit_per_minute` | `1200`（Binance 默认） |
| `rest_429_initial_backoff_ms` | `1000` |
| `rest_circuit_breaker_threshold` | 连续 3 次 429/418 触发熔断 |

---

## 9. 不变量

1. connected=true 不能推出 healthy=true。
2. 关键流 STALE ⇒ confidence_state 必须 UNKNOWN，不得伪造 CONFIDENT。
3. trade_id 去重必须在进入 Feature Engine 之前完成。
4. OI as-of lookup 容差外无数据 ⇒ unavailable，不得回退取更旧数据。
5. HealthStatus 必须通过统一事件暴露给 Feature Engine 与 UI，不得只存内存。
