# RUNTIME_ARCHITECTURE — 运行时架构

| 版本 | v1.0 |
|------|------|
| 依据 | 改造任务文档 §4-§10/§25-§26/§29-§31, ARCHITECTURE.md |

## 1. 编排层

`src/runtime.py` 的 `MarketRadarRuntime` 是唯一运行时编排入口，串联：

| 组件 | 职责 | 实例化于 |
|---|---|---|
| SymbolUniverse | 动态 universe（exchangeInfo + 24h ticker 排序过滤） | runtime |
| LightScanner | Stage1 低成本扫描 → 候选 | runtime |
| DeepScanner | Stage2 候选深度分析（aggTrade/Kline WS + OI/Funding REST） | runtime |
| FreshnessWatchdog | 每流 freshness budget（connected≠healthy） | runtime |
| ConfidenceTracker | 关键流 → CONFIDENT/DEGRADED/UNKNOWN | state_machine |
| OILookup | OI 带容差时间对齐 | runtime |
| QueueLagMonitor | bounded queue 深度 + lag | runtime |
| RateLimiter | 统一 REST 限频（429/418/熔断） | runtime |
| FeatureEngine | 多窗口 FeatureSnapshot | runtime |
| StateMachine | 12 转移 + detector + confidence 约束 | runtime |

## 2. 关键不变量（已修复 P0）

1. **无演示 confidence 覆盖**：confidence 由 FreshnessWatchdog + ConfidenceTracker 真实派生。stale → UNKNOWN → 禁止 CONFIRMED。
2. **Evidence 不被 refresh 擦除**：`latest_state`（CurrentState）与 `last_evidence_transition`（含证据的 TransitionEvent）分离。自动 COOLDOWN 迁移（无 evidence）不擦除之前的证据。
3. **多时间窗口**：统一 WindowManager（5s/15s/30s/1m/5m trade flow + 1m/5m/15m/1h kline），避免重复内存。
4. **CVD 每笔只计一次**：add_trade 时更新 CVD（修复旧编排层重叠窗口重复计数）。
5. **bounded queue**：trade queue maxsize=50000，满则丢最旧 + 告警，防积压。
6. **统一 RateLimiter**：所有 REST（OI/Funding/ticker）经限频，无各自重试。

## 3. 两阶段 Radar

- Stage1 LightScanner：每 `light_scan_interval_s` 用 24h ticker（1 请求）扫全 universe，跨扫描 baseline 计算 relative_volume_z / trade_count_z + price_change → 候选排名。
- Stage2 DeepScanner：候选（≤`deep_max_symbols`）跑完整 pipeline。候选集 set 变化才重建 WS（防抖动）。

## 4. WS 生命周期（§26）

BaseWSCollector 实现：auto reconnect + 指数退避 + resubscribe + last message/exchange event tracking + 连接统计。
FreshnessWatchdog 独立判断 healthy（不依赖 socket.open）：`now − last_event_time > budget` → STALE。

## 5. 配置化（§30）

所有阈值在 `configs/`：universe size / liquidity floor / window length / stale threshold / anomaly/OI/delta/retrace threshold / cooldown / rate limit / candidate count / poll interval。`src/config/__init__.py` Pydantic schema 校验，非法 fail closed。

## 6. 分层职责（§31，不可逾越）

- Collector 只采集/标准化；Feature 只计算；Detector 只分析；UI 只消费 AnalysisEvent/CurrentState。
- 任何 Detector 不直接请求 Binance；任何 Collector 不决定 LONG/SHORT；任何 Dashboard 不复制业务逻辑。
