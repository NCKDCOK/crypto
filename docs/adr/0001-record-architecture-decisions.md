# ADR-0001: 记录架构决策

| 字段 | 值 |
|------|----|
| 状态 | 已接受 |
| 日期 | 2026-08-25 |
| 决策者 | 项目负责人 |
| 相关 ADR | — |

## 背景

本项目以 AI Coding Agent 为主要开发者，开发速度不可预测、迭代频繁。SYSTEM_DESIGN.md 与 AI_CODING_AGENT_MANUAL.md 已确立 Gate-based 流程，AI_RULES.md 第 8 条规定"已验收接口视为稳定 contract；未经 ADR 不得大规模重构"。因此需要一种轻量、持久、可被 Agent 检索的机制来记录影响 contract 与架构的决策。

## 决策

采用 ADR（Architecture Decision Record）记录所有影响 contract、分层边界、数据单位、阈值体系或已验收接口的架构决策。

- ADR 存放于 `docs/adr/`，编号从 0001 起递增。
- 每个 ADR 使用 `0000-template.md` 格式。
- ADR 状态流转：提议 → 已接受 →（可能）已废弃 / 已取代。
- 已接受后的 ADR 是 contract 变更的前置条件：任何违反已接受 ADR 的改动必须先提交新 ADR 取代或补充。

## 理由

- Gate 流程要求"锁定 contract"，ADR 提供可追溯的锁定证据。
- Agent 可在执行任务前检索相关 ADR，避免无意破坏已验收接口。
- 轻量文本格式，AI 生成/审查成本低。

## 备选方案

- 不记录决策，只靠 spec 文档：spec 描述"是什么"，但不记录"为什么这么选"，重构时易反复争论已决问题。
- 集中到一个大决策文档：随决策增多会变得臃肿、难以检索。

## 后果

- 正面：contract 变更有据可查；Reviewer Agent 可引用具体 ADR 审查改动。
- 负面：每个架构变更需多写一份 ADR，轻微增加开销。
- 后续：首批 ADR 应记录 aggressor_side 映射、OI 单位选择、Fail Closed 策略等已锁定决策（后续按需补充）。

## 合规检查

不违反任何 AI_RULES.md 硬规则；直接支撑第 8 条的实现。
