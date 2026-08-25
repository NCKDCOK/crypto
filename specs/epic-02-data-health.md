# Epic 02 — Data Health & 时间对齐（Gate 2）

| 字段 | 值 |
|------|----|
| Gate | 2 |
| 依赖 | epic-01 |
| 关联文档 | DATA_HEALTH.md; DATA_MODEL.md §0.3,§5; STATE_MACHINE.md §4 |
| 禁止 | 信号 / 机会分 / Detector / UI 业务逻辑 |

## Goal

解决"WS 看似连接但数据不推"和 REST 时间错位问题。让 confidence_state 正确派生并约束状态机。

## Context

epic-01 产出了标准化事件，但尚未判断数据是否真正可用。本 Gate 建立独立 freshness watchdog、去重校验、OI as-of lookup 与 ConfidenceState 派生，为 Feature Engine 和 Detector 提供 fail closed 基础。

## Inputs

- DATA_HEALTH.md（freshness budget、HealthLevel、ConfidenceState 派生规则、as-of lookup、限频）
- DATA_MODEL.md §5 HealthStatus / §0.3 ConfidenceState
- STATE_MACHINE.md §4 Data Health 对状态机的硬约束

## Outputs

1. `src/health/freshness_watchdog.py`：每流独立 freshness budget 判断，connected ≠ healthy。
2. `src/health/dedup.py`：trade_id 去重校验（确认在 Feature Engine 入口前生效）。
3. `src/health/oi_lookup.py`：OI as-of lookup，带容差，容差外返回 unavailable。
4. `src/health/confidence.py`：从关键流 HealthLevel 派生 ConfidenceState。
5. HealthStatus 通过统一事件暴露给 Feature Engine 与 UI。
6. queue/lag 监控：receive lag、event lag、queue backlog。

## Invariants

- connected=true 不能推出 healthy=true。
- 关键流 STALE/DRIFT/FAIL ⇒ confidence_state=UNKNOWN。
- trade_id 去重在进入 Feature Engine 前完成。
- OI as-of lookup 容差外无数据 ⇒ unavailable，不得回退取更旧数据。
- HealthStatus 必须通过事件暴露，不得只存内存。

## Out of Scope

Feature 计算 / Detector / UI 业务逻辑。

## Tests

见 DATA_HEALTH.md §5 必测场景，每场景一个 fixture：

1. WS open 但 30s 无 aggTrade → STALE。
2. 重连后重复 trade → 丢弃，CVD 前置不双计。
3. 5m 前无接近 OI 快照 → oi_change_5m=unavailable（非取 9m 前）。
4. 429 + Retry-After → 全局退避，不各模块重试。
5. 关键数据 STALE → detector 无权进入 CONFIRMED。
6. WS 半死（TCP open 无数据）→ budget 内降级 STALE。

## Observability

- metrics: stream_age_ms / stream_status / queue_depth{stage} / receive_lag_ms
- log: stale 降级 / drift 检测 / 容差外 lookup

## Definition of Done

| 项 | 验收 |
|----|------|
| 6 个必测场景全绿 | fixture 测试 |
| confidence_state 正确派生 | 关键流状态变化 → ConfidenceState 联动测试 |
| OI lookup 容差行为 | 边界测试 |
| 无信号/Detector 逻辑 | 代码审查 |

## Files Allowed

`src/health/`, `fixtures/`, `tests/`, `configs/data_health.yaml`

## 推荐任务拆分

- 02-A: freshness watchdog（含 symbol 分级 budget）
- 02-B: dedup 校验确认
- 02-C: OI as-of lookup + 容差
- 02-D: ConfidenceState 派生 + 事件暴露
- 02-E: queue/lag 监控
