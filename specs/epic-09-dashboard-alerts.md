# Epic 09 — Dashboard & Alerts（Gate 9）

| 字段 | 值 |
|------|----|
| Gate | 9 |
| 依赖 | epic-08 |
| 关联文档 | SYSTEM_DESIGN.md §11,§16; AI_CODING_AGENT_MANUAL.md §13; ARCHITECTURE.md §3 |
| 禁止 | UI 重新实现业务逻辑 / LLM 覆盖状态机 / UI 订阅原始行情自行计算 |

## Goal

把分析结果做成"市场雷达"，但 UI 绝不能重新实现业务逻辑。

## Context

epic-08 后系统可验证。本 Gate 做展示与提醒层。UI 只消费 AnalysisEvent，AI 解读只翻译不决策。

## Inputs

- SYSTEM_DESIGN.md §11 Evidence-first 输出 / §16 合规措辞
- AI_CODING_AGENT_MANUAL.md §13 Gate 9 页面清单
- DATA_MODEL.md §7 AnalysisEvent

## Outputs

1. `src/api/`：FastAPI endpoints + WebSocket（推送 AnalysisEvent）。
2. 前端 React + TS + Vite：
   - Market Radar：symbol/price/24h/state/direction/data health/evidence 摘要
   - Symbol Detail：时间线/特征/Evidence/Veto/State transition/OI/CVD/Delta 图
   - Data Health：每流 freshness/reconnect/message rate/429/REST 状态
   - Signal History：CONFIRMED/REJECTED/WITHDRAWAL 历史 + outcome
   - Alert 配置：阈值与冷却
3. `src/alerts/`：Telegram 等，仅消费 AnalysisEvent（START_CONFIRMED/EXHAUSTION/WITHDRAWAL）。
4. AI 解读：读取 AnalysisEvent 结构化结果翻译成人话，不得重新决定方向或覆盖状态。

## Invariants

- UI 只消费 AnalysisEvent，不订阅原始行情自行计算。
- LLM 只读 AnalysisEvent 生成自然语言，不得覆盖 new_state / direction / 阈值。
- 所有提醒明确为行情分析信号，不保证收益，不自动执行交易。
- "主力资金/资金进入"为通俗表述，详情页须解释为统计推断。
- UI 不得复制 CVD/OI/efficiency 等计算逻辑。

## Out of Scope

自动下单 / 仓位管理 / 机会分 / calibrated score。

## Tests

1. API 返回结构化 AnalysisEvent，UI 可渲染。
2. WebSocket 推送状态变化。
3. Alert 在 START_CONFIRMED/EXHAUSTION/WITHDRAWAL 触发，冷却生效。
4. AI 解读输出不覆盖 new_state（只读断言）。
5. UI 无业务计算逻辑（代码审查）。

## Observability

- metrics: api_request_total / alert_sent_total{state} / ws_connections
- log: alert 触发/冷却

## Definition of Done

| 项 | 验收 |
|----|------|
| 排行榜可见 state/direction/health | UI |
| 详情页可展开完整证据链 | UI |
| Alert 触发 + 冷却 | 测试 |
| AI 解读只读 | 不覆盖状态 |
| 无自动下单/API Key | 范围审查 |
| UI 无业务计算 | 代码审查 |

## Files Allowed

`src/api/`, `src/alerts/`, `frontend/`, `tests/`, `configs/`(alert 配置)

## 推荐任务拆分

- 09-A: FastAPI + WebSocket
- 09-B: Market Radar + Symbol Detail
- 09-C: Data Health 面板
- 09-D: Signal History
- 09-E: Alerts (Telegram) + 冷却
- 09-F: AI 解读（只读翻译）
