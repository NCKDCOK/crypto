# 测试策略（Testing）

| 版本 | v1.0 |
|------|------|
| 依据 | SYSTEM_DESIGN.md §17, AI_CODING_AGENT_MANUAL.md |
| 状态 | 已锁定 |

---

## 0. 核心原则

1. **不得通过降低测试标准、删除断言、扩大 tolerance 来"修复"失败。**
2. 新增核心逻辑必须同时提交单元测试、fixture 或 replay test。
3. 测试必须可离线运行（不依赖真实 Binance 连接）。
4. 相同 replay 输入 → 相同 feature/state 输出（deterministic）。

---

## 1. 测试栈

| 层 | 工具 | 用途 |
|----|------|------|
| 单元 | pytest | 纯函数/契约 |
| 异步 | pytest-asyncio | collector / event loop |
| 性质 | hypothesis | 不变量性质测试（去重、窗口边界） |
| Replay | 自建 harness | 确定性重放 |

---

## 2. 测试分层

### 2.1 Contract tests（Gate 0）

- 每个 domain 对象的 JSON 序列化/反序列化 round-trip 稳定。
- 枚举值全覆盖。
- 时间字段类型与单位（UTC ms）一致。
- `quote_notional == price × qty`。
- aggressor_side 与 is_maker 映射正确（`m=true → SELL`，含单元测试）。

### 2.2 Collector tests（Gate 1）

- 固定 fixture（录制的 Binance WS/REST 响应）能得到预期事件。
- 强制断线恢复：模拟断连后重连，trade_id 去重生效。
- 1m Kline 仅 `is_closed=true` 进入慢周期确认。
- REST RateLimiter：模拟 429 + Retry-After，不产生 retry storm；熔断触发。

### 2.3 Data Health tests（Gate 2）

见 DATA_HEALTH.md §5 必测场景。每个场景一个 fixture。

### 2.4 Feature tests（Gate 3）

- 每个 feature 用**手算 fixture** 验证：输入已知 trade/kline 序列 → 断言精确特征值。
- 边界：空窗口、单条事件、窗口淘汰、重连重复事件不污染 CVD。
- 价格变动但 contracts 不变时 `oi_change=0`。
- 缺数据 → `null` / `unavailable`，不抛异常、不取旧值。

### 2.5 Detector tests（Gate 4–7）

- 每个检测器：正例触发 + 负例不触发 + veto 命中路径。
- 每次状态转移必须产出 evidence + veto；无证据转移非法。
- data_stale hard veto：关键输入 stale 时禁止 CONFIRMED。

### 2.6 Replay regression tests（Gate 8+）

- 相同 fixture 重放两次，状态序列与特征输出逐字节一致。
- 升级代码后历史 fixture 状态序列无意外变化（regression baseline）。

---

## 3. Fixture 规范

- 存放 `fixtures/`，按 `{stream}_{scenario}.jsonl` 命名。
- 每条 fixture 附 `meta`：来源、录制时间、symbol、预期结果摘要。
- 录制的是**原始 Binance 响应**（未标准化），由 collector 解析，保证测试覆盖真实解析路径。
- 合成 fixture（手造数据）必须标注 `synthetic: true`，并附手算期望值。

### 推荐场景 fixture

| fixture | 场景 | 用于 |
|---------|------|------|
| `aggtrade_normal.jsonl` | 正常随机噪声 | anomaly 不触发 |
| `aggtrade_volume_spike.jsonl` | volume+trade_count 同步尖峰 | anomaly 触发 |
| `aggtrade_single_large.jsonl` | 单笔大单、trade_count 无变化 | 区分假启动 |
| `aggtrade_reconnect_dup.jsonl` | 重连后重复 trade_id | 去重 |
| `aggtrade_stale_freeze.jsonl` | 30s 无成交 | STALE + hard veto |
| `oi_price_up_flat.jsonl` | 价格涨 OI 不变 | oi_change=0 |
| `oi_contraction_cover.jsonl` | price↑ vol↑ OI↓ | squeeze 分类 |
| `kline_closed_only.jsonl` | 仅 closed bar 确认 | 慢周期 |
| `rest_429_retry.jsonl` | 429 + Retry-After | 限频退避 |
| `startup_long_clean.jsonl` | 干净多头启动 | START_CONFIRMED |
| `startup_rejected_retrace.jsonl` | 拉升后完整回吐 | REJECTED |
| `withdrawal_divergence.jsonl` | 价创新高 CVD/OI 不确认 | WITHDRAWAL |

---

## 4. 确定性 Replay 要求

1. 事件按 `event_time` 顺序重放（同时间按 trade_id/序列号）。
2. 使用**固定 clock**（test clock），不依赖 wall time。
3. FeatureSnapshot 与状态序列必须可复现。
4. replay harness 与 clock abstraction 在 Gate 0 建空骨架。

---

## 5. DoD（Definition of Done）测试门槛

| 领域 | 门槛 |
|------|------|
| 数据 | 核心 P0 数据连续运行；断线可重连；重复 trade 不污染；STALE 可识别 |
| 限频 | REST 集中 RateLimiter、429 Retry-After、熔断；无请求风暴 |
| 特征 | CVD/OI/Volume/Delta/efficiency 可用固定 fixture 复算一致 |
| 检测 | 每次状态转换有 evidence 与 veto；无无法解释的"87 分" |
| 回放 | 相同输入重放两次，状态序列与特征输出一致 |
| UI | 排行榜可见 state/direction/health；详情页可展开证据链 |
| 范围 | 无自动下单、仓位管理、API Key 逻辑 |

---

## 6. 禁止清单

- 禁止用 `sleep` 模拟时间推进（用 test clock）。
- 禁止为过测试放宽阈值或 tolerance。
- 禁止只测 happy path。
- 禁止 mock 掉被测层自身的逻辑（如 mock 去重函数来测 CVD）。
- 禁止测试连真实 Binance。
