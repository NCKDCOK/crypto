# AI_RULES — 硬规则

> 每个 Agent 开始任务前**必须**阅读本文件、ARCHITECTURE.md 和当前 epic spec。不得只读用户一句自然语言就直接改代码。

本系统是**资金行为驱动的实时行情分析系统**，不是自动交易系统。以下硬规则不可协商。

---

## 硬规则

1. **本项目不是自动交易系统。** 禁止实现下单、API Key、账户、仓位、杠杆和订单管理。

2. **核心目标是资金行为分析。** 未经 spec 明确要求，不得主动加入 RSI、MACD、EMA、OB、FVG 等传统指标。

3. **数据异常必须 fail closed。** 宁可 `UNKNOWN` / `DATA_STALE`，不得用陈旧数据继续产生确认信号。关键输入 STALE ⇒ confidence_state=UNKNOWN ⇒ 禁止 CONFIRMED。

4. **所有阈值配置化。** 禁止把 magic number 散落在业务代码。阈值集中在 `configs/`。

5. **所有状态变化必须产生 machine-readable evidence 与 veto。** 禁止只返回一个 score。无证据的状态转移非法。

6. **AI 不得自行决定权重，不得自行创造"机会分公式"。** V1 只输出 Evidence + State；评分延后到 Gate 8 历史样本校准后。

7. **分层职责不可逾越。** Collector 只能采集/标准化；Feature 只能计算；Detector 只能分析；UI 不能复制业务逻辑（只消费 AnalysisEvent）。

8. **已验收接口视为稳定 contract。** 未经 ADR（Architecture Decision Record）不得大规模重构。

9. **新增核心逻辑必须同时提交单元测试、fixture 或 replay test。**

10. **不得通过降低测试标准、删除断言、扩大 tolerance 来"修复"失败。**

11. **外部开源代码必须记录来源、许可证和修改点。** "参考思想"与"复制源代码"是两个不同动作；复制前必须核查 LICENSE。

12. **每次任务只完成当前 spec，不顺手增加未请求功能。**

---

## 方向安全（P0）

13. **aggressor_side 映射不可写反。** Binance `m=true` ⇒ 买方是 maker ⇒ 卖方主动 ⇒ `aggressor_side=SELL`。任何实现必须对此写单元测试。写反则 CVD/Taker Delta 全量反转。

14. **OI 用基础资产数量，不用美元名义。** 价格涨但 `open_interest` 不变 ⇒ `oi_change=0`。不得把名义 OI 上涨误判为新增仓位。

15. **connected ≠ healthy。** WS socket open 但无数据推送必须被 freshness watchdog 降级为 STALE，不得当作健康。

16. **trade_id 去重必须在进入 Feature Engine 前完成。** 重连后重复成交不得污染 CVD。

---

## 任务边界

每个 AI Task 必须包含以下字段（见 MANUAL §3）：

`Goal` / `Context` / `Inputs` / `Outputs` / `Invariants` / `Out of Scope` / `Tests` / `Observability` / `Definition of Done` / `Files Allowed`

- 一个 Task 最好能在单次 Agent 会话中实现和审查。
- 不要给"实现整个 Binance 数据系统"这种大任务。
- 先只读代码并给出不超过 10 条实施计划，计划通过后再实现。
- 完成后列出改动文件、测试命令、测试结果和仍存在的风险。

---

## AI 最易犯的错误

| 错误 | 危险 | 预防 |
|------|------|------|
| 一口气实现整个系统 | 接口漂移、模块互抄逻辑 | 任务切碎 + Gate |
| 擅自加入传统指标 | 偏离资金行为核心 | AI_RULES + Out of Scope |
| 现场发明权重 | 虚假精确感 | Evidence-first，评分延后 |
| 遇缺数据用上一次值 | 造假信号 | Health + fail closed |
| 把美元 OI 当新增仓位 | 价格涨名义 OI 也涨 | contracts 优先 |
| connected=true 当数据正常 | WS 半死被忽略 | freshness watchdog |
| 重连后重复累计 trade | CVD 污染 | trade_id dedup |
| 为过测试放宽阈值 | 掩盖实现错误 | 禁止改测试标准 |
| 前端复制一套计算 | 结果不一致 | UI 只消费 AnalysisEvent |
| 大规模"顺手重构" | 已验收模块失稳 | 文件范围 + ADR |
