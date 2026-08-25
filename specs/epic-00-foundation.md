# Epic 00 — Foundation / 项目骨架（Gate 0）

| 字段 | 值 |
|------|----|
| Gate | 0 |
| 依赖 | 无 |
| 关联文档 | SYSTEM_DESIGN.md §5,§14; DATA_MODEL.md; ARCHITECTURE.md; TESTING.md |
| 禁止 | 任何 Binance 网络调用、collector、指标、Detector、UI |

## Goal

先锁定 contracts、目录、配置与测试框架，不接真实交易所。建立可确定性 replay 的骨架。

## Context

所有后续 Gate 依赖本 epic 产出的稳定 contract。DATA_MODEL.md 已锁定全部事件对象字段、枚举与映射规则。

## Inputs

- DATA_MODEL.md（TradeEvent / KlineEvent / OpenInterestSnapshot / FundingRateSnapshot / HealthStatus / FeatureSnapshot / AnalysisEvent）
- STATE_MACHINE.md（State 枚举、转移 guard、配置项）
- ARCHITECTURE.md（目录结构、Clock、存储/配置接口）
- TESTING.md（contract test 要求）

## Outputs

1. `src/domain/`：全部 Pydantic 模型 + 枚举（AggressorSide / Direction / ConfidenceState / HealthLevel / KlineInterval / State / EvidenceFamily / VetoType）。
2. `src/clock/`：Clock 接口 + SystemClock + TestClock。
3. `src/config/`：配置加载与 Pydantic schema 校验（app/data_health/features/detectors/state_machine/symbols）。
4. `src/storage/`：Repository 接口空骨架（save_event / save_feature_snapshot / save_analysis_event / get_oi_snapshot_asof / list_transitions）。
5. `configs/`：全部 yaml 模板（含默认值，见各 docs）。
6. `tests/`：pytest + pytest-asyncio + hypothesis 框架就绪。
7. `fixtures/` 与 `replay/` 空骨架。
8. AI_RULES.md / ARCHITECTURE.md 已就位（本仓库已有）。
9. CI：lint + test + type check。

## Invariants

- 时间字段一律 UTC ms (int64)。
- aggressor_side 映射 `m=true → SELL`（须有单元测试）。
- `quote_notional == price × qty`。
- OI 单位 = 基础资产数量（字段名 `open_interest`）。
- 配置 schema 校验失败 ⇒ fail closed 不启动。

## Out of Scope

- 任何 Binance WS/REST 调用。
- CVD / OI 增速 / 信号 / 机会分 / Detector / UI。

## Tests

1. 每个 domain 对象 JSON round-trip 稳定。
2. 枚举值全覆盖。
3. `quote_notional == price × qty` 断言。
4. aggressor_side × is_maker 映射 4 种组合测试。
5. 配置加载合法/非法用例。
6. Clock.now_ms() 在 SystemClock/TestClock 下行为正确。
7. 无任何网络调用（离线全绿）。

## Observability

- structured logging 框架就绪。
- 配置加载日志。

## Definition of Done

| 项 | 验收方式 |
|----|----------|
| 领域对象序列化/反序列化稳定 | contract tests 绿 |
| 没有任何 Binance 网络调用 | 离线测试全部运行 |
| CI 绿色 | lint + test + type check |
| AI_RULES / ARCHITECTURE / ADR 模板入库 | 人工审查 |
| Clock / Repository / Config 骨架可被后续 Gate import | import 成功 |

## Files Allowed

`src/domain/`, `src/clock/`, `src/config/`, `src/storage/`, `configs/`, `tests/`, `fixtures/`, `src/replay/`(空骨架), `pyproject.toml`, `conftest.py`

## 推荐任务拆分

- 00-A: domain 模型 + 枚举 + contract tests
- 00-B: clock 抽象 + 测试
- 00-C: config 体系 + schema 校验
- 00-D: storage 接口骨架 + CI 配置
