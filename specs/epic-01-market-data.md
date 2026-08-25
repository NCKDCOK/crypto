# Epic 01 — Market Data Gateway（Gate 1）

| 字段 | 值 |
|------|----|
| Gate | 1 |
| 依赖 | epic-00 |
| 关联文档 | DATA_MODEL.md §1-4; DATA_HEALTH.md §6,§8; ARCHITECTURE.md §3 |
| 禁止 | CVD、OI 增速、信号、机会分、数据库分析逻辑、UI |

## Goal

把公开 Binance Futures 数据稳定地变成统一事件。此阶段不计算 CVD、不判断启动。

## Context

epic-00 已提供 domain contracts。本 Gate 把 Binance WS/REST 响应解析为标准化事件，交由 Health 层标注。先证明数据输入可控。

## Inputs

- DATA_MODEL.md §1 TradeEvent / §2 KlineEvent / §3 OpenInterestSnapshot / §4 FundingRateSnapshot
- Binance Futures public API 文档（aggTrade / kline / openInterest / premiumIndex）
- DATA_HEALTH.md §8 REST 限频要求

## Outputs

1. `src/collectors/`：aggTrade Collector、Kline Collector、OI Poller、Funding/Premium Collector。
2. `src/collectors/symbol_registry.py`：USDT-M 交易对发现、过滤下架/无效 symbol。
3. `src/health/rate_limiter.py`：集中 REST RateLimiter（权重预算、Retry-After、指数退避、熔断）。
4. 解析 + 标准化 + 去重（trade_id 去重在 collector 出口完成）。

## Invariants

- trade_id 同 symbol 严格递增；重连后 ≤ 已见最大值则丢弃。
- aggressor_side 映射 `m=true → SELL`。
- quote_notional 本地计算 = price × qty。
- 仅 `is_closed=true` 的 Kline 进入慢周期确认标记。
- OI 单位 = 基础资产数量。
- REST 集中限频，禁止各模块各自轮询。
- OI poller 错误不阻塞其他 symbol。

## Out of Scope

CVD / Taker Delta / 信号 / Detector / UI / 数据库写入业务逻辑。

## Tests

1. 固定 fixture（录制 Binance 响应）→ 预期标准化事件。
2. 强制断线恢复 + 重连后 trade_id 去重（CVD 前置数据不双计）。
3. 1m Kline 仅 closed bar 标记正确。
4. OI poller 速率受控；单 symbol 错误不阻塞其他。
5. Funding/Premium 字段单位与时间戳 fixture 测试。
6. RateLimiter：模拟 429 + Retry-After，不产生 retry storm；熔断触发。
7. Symbol Registry：固定 fixture 得到预期 symbol 列表，下架 symbol 被过滤。

## Observability

- metrics: stream_age_ms / stream_status / reconnect_count / rest_weight_used / rest_429_total / rest_418_total
- structured log: 连接/订阅/断线/重连/429/熔断

## Definition of Done

| 项 | 验收 |
|----|------|
| aggTrade 连续运行 | 断线可重连，trade_id 不重复 |
| Kline | 仅 closed bar 标记正确 |
| OI poller | 速率受控，错误不阻塞其他 symbol |
| RateLimiter | 模拟 429 无 retry storm，熔断生效 |
| 无 CVD/信号/UI 逻辑 | 代码审查 |

## Files Allowed

`src/collectors/`, `src/health/rate_limiter.py`, `src/collectors/symbol_registry.py`, `fixtures/`, `tests/`, `configs/symbols.yaml`

## 推荐任务拆分

- 01-A: Symbol Registry
- 01-B: aggTrade Collector + reconnect + dedup
- 01-C: Kline Collector (closed bar)
- 01-D: OI Poller + time-aligned snapshot
- 01-E: Funding/Premium Collector
- 01-F: REST RateLimiter
