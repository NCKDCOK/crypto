# Epic 08 — Replay、Labeling 与统计（Gate 8）

| 字段 | 值 |
|------|----|
| Gate | 8 |
| 依赖 | epic-07 |
| 关联文档 | SYSTEM_DESIGN.md §12; ARCHITECTURE.md §4,§5; TESTING.md §4 |
| 禁止 | 在此之前讨论机会分/权重/概率（Gate 8 之后才允许） |

## Goal

把系统从"看起来厉害"变成"能被历史样本验证"。

## Context

epic-07 后系统功能完整。本 Gate 建立确定性 replay 与标注体系，为未来评分校准提供真实标签。只有到这里以后才允许讨论机会分/权重/概率。

## Inputs

- SYSTEM_DESIGN.md §12（replay + labeling）
- ARCHITECTURE.md §4 Clock（TestClock）/ §5 Storage（list_transitions）
- TESTING.md §4 确定性 replay 要求

## Outputs

1. `src/replay/engine.py`：事件时间顺序重放，固定 TestClock，deterministic。
2. `src/replay/labeling.py`：人工标注接口（false_start / continuation / squeeze / absorption / withdrawal）。
3. `src/replay/outcome.py`：每个候选生成 outcome（最大有利/不利变动、持续时间、回撤、是否快速失败）。
4. `src/replay/report.py`：简单报表（每个 veto 命中后后续表现、每个 evidence 条件频率）。
5. 保存原始事件子集 + FeatureSnapshot + 状态转换 + evidence/veto。

## Invariants

- 相同输入重放两次 → 状态序列与特征输出逐字节一致。
- 使用 TestClock，不依赖 wall time。
- 重放按 event_time 顺序（同时间按 trade_id/序列号）。
- 标签不得回写改变状态机（只记录）。

## Out of Scope

calibrated score / 机会分（v1.5/v2）/ UI。

## Tests

1. 同一 fixture 重放两次 → 输出一致。
2. 升级代码后历史 fixture regression baseline 不意外变化。
3. outcome 计算正确（已知序列手算）。
4. 标注接口读写正确。

## Observability

- log: replay 开始/结束/事件数/状态序列

## Definition of Done

| 项 | 验收 |
|----|------|
| replay deterministic | 两次重放一致 |
| regression baseline | 升级后无意外变化 |
| outcome 生成 | 已知序列一致 |
| 标注接口可用 | 读写测试 |
| 报表生成 | veto/evidence 频率统计 |

## Files Allowed

`src/replay/`, `tests/`, `fixtures/`, `src/storage/`(读路径)

## 推荐任务拆分

- 08-A: replay engine + TestClock 推进
- 08-B: 事件/快照持久化读取
- 08-C: outcome 计算
- 08-D: 标注接口
- 08-E: 报表
