# Runtime Integration Audit（运行时集成审计）

| 版本 | v1.0 |
|------|------|
| 依据 | 改造任务文档 §三 / AI_RULES / ARCHITECTURE / DATA_HEALTH |
| 状态 | Audit 完成，进入 P0 改造 |

本文对 `src/` 全部重要模块做集成审计：**已实现 / 已实例化 / Runtime 已接入 / 收到真实数据 / 被谁消费 / 测试覆盖 / 问题**。
审计基线：`pytest -q` → 289 passed。模块本身实现质量良好，问题集中在 **runtime 编排层（`src/main.py`）只接入了 aggTrade 单流**。

---

## 1. 模块审计表

| 模块 | 文件 | 已实现 | 已实例化 | Runtime已接入 | 收到真实数据 | 被谁消费 | 测试覆盖 | 问题 |
|---|---|---|---|---|---|---|---|---|
| AggTradeCollector | collectors/aggtrade_collector.py | ✅ | ✅ main.py:67 | ✅ | ✅(live WS) | `_on_trade`→RollingWindow | ✅ | OK；但 dedup 在 collector 内，未与 FreshnessWatchdog 联动 |
| KlineCollector | collectors/kline_collector.py | ✅ | ❌ | ❌ | ❌ | — | ✅ | **orphan**：写好从未在 main.py 实例化 |
| OIPoller | collectors/oi_poller.py | ✅ | ❌ | ❌ | ❌ | — | ✅ | **orphan**：从未实例化；FeatureEngine 的 oi_snapshots 永远为空 → oi_change 恒 None |
| FundingCollector | collectors/funding_collector.py | ✅ | ❌ | ❌ | ❌ | — | ✅ | **orphan**：从未实例化；funding 特征缺失 |
| SymbolRegistry | collectors/symbol_registry.py | ✅ | ❌ | ❌ | ❌ | — | ✅ | **orphan**：universe 硬编码 BTC/ETH/SOL |
| FeatureEngine | features/engine.py | ✅ | ✅ main.py:55 | ⚠️ 半接入 | ⚠️ 仅 trades | StateMachine | ✅ | 单窗口；OI/funding 快照从未注入；data_health 字段恒 `{}`；缺 price/多窗口/efficiency 完整集 |
| RollingWindow | windows/rolling_window.py | ✅ | ✅(单窗口) | ⚠️ | — | FeatureEngine | ✅ | `WindowManager` 已存在但 main.py 仍用单一 `RollingWindow(30s)` |
| FreshnessWatchdog | health/freshness_watchdog.py | ✅ | ❌ | ❌ | ❌ | — | ✅ | **orphan**：从未注册流；connected≠healthy 未在 runtime 生效 |
| QueueLagMonitor | health/queue_lag_monitor.py | ✅ | ❌ | ❌ | ❌ | — | ✅ | **orphan**：无 bounded queue 监控 |
| ConfidenceTracker | health/confidence.py | ✅ | ✅(被 StateMachine 持有) | ❌ | ❌ | StateMachine | ✅ | **P0.1 演示覆盖**：main.py:107 直接写 `_confidence[symbol]=CONFIDENT`，绕过 Health 派生 |
| RateLimiter | health/rate_limiter.py | ✅ | ❌ | ❌ | ❌ | — | ✅ | **orphan**：OI/Funding collector 未经统一限频（因 collector 未启动） |
| OILookup | health/oi_lookup.py | ✅ | ❌ | ❌ | ❌ | — | ✅ | **orphan**：FeatureEngine 自带 oi_snapshots 列表，未用带容差的 OILookup |
| AnomalyDetector | detectors/anomaly.py | ✅ | ✅(StateMachine 持有) | ✅ | ⚠️ 缺 OI | StateMachine | ✅ | 输入 FeatureSnapshot 缺 OI/price/多窗口特征 |
| StartupDetector | detectors/startup.py | ✅ | ✅ | ✅ | ⚠️ | StateMachine | ✅ | OI 证据恒 unavailable；缺 price_efficiency/acceptance |
| FalseStartFilter | detectors/false_start.py | ✅ | ✅ | ✅ | ⚠️ | StateMachine | ✅ | 缺 ONE_BAR_SPIKE veto；data_stale 仅靠 `is_confident` 布尔，未读真实 health |
| Continuation/Exhaustion/Withdrawal | detectors/continuation_withdrawal.py | ✅ | ✅ | ✅ | ⚠️ | StateMachine | ✅ | 缺 CVD divergence 历史（无新高对比）、OI decay 趋势；单窗口判断 |
| StateMachine | state_machine/machine.py | ✅ | ✅ | ✅ | — | Dashboard/Alerts/Repo | ✅ | confidence 由 main.py 演示覆盖；转移 guard 未严格读 health |
| Repository | storage/__init__.py | ✅ InMemory | ✅ | ✅ | — | Dashboard/Replay | ✅ | 两个 InMemoryRepository 实例（main.py:57,59）不一致：dashboard 与 save 分离 |
| Replay | replay/engine.py | ✅ | ❌(runtime) | — | — | 测试 | ✅ | 仅 trades 单窗口；用 `confidence_overrides` 绕过 Health（违反§19 同逻辑） |
| AlertManager | alerts/manager.py | ✅ | ✅ | ✅ | — | — | ✅ | OK |
| DashboardService | api/dashboard.py | ✅ | ✅ | ✅ | — | main.py HTML | ✅ | **P0.2**：无 latest_state/last_transition 分离；`update_event` 把刷新用的空 evidence 事件覆盖真实 transition |

---

## 2. 重点问题清单

### 2.1 Orphan module（写了但 runtime 从未接入）
- `KlineCollector` / `OIPoller` / `FundingPremiumCollector` / `SymbolRegistry`
- `FreshnessWatchdog` / `QueueLagMonitor` / `RateLimiter` / `OILookup`
- 它们都有完整实现与单测，但 `main.py` 没有实例化/启动，导致真实 pipeline 只有 aggTrade 单流。

### 2.2 Demo override（违反 P0.1）
- `main.py:107` `confidence._confidence[symbol] = ConfidenceState.CONFIDENT`（注释明写"演示"）
- `main.py:146` `_refresh_dashboard` 再次写 `CONFIDENT`
- `replay/engine.py:93` `confidence_overrides` 直接注入 CONFIDENT，绕过 Health 派生
- → 必须：confidence 由 FreshnessWatchdog + ConfidenceTracker 真实派生，stale 时 UNKNOWN，Fail Closed。

### 2.3 Dashboard Evidence 被覆盖（违反 P0.2）
- `main.py:137` `_refresh_dashboard` 构造 `AnalysisEvent(evidence=[], vetoes=[], previous=new=当前state)` 调 `dashboard.update_event`，把真实 transition 的 evidence 擦掉。
- → 必须：分离 `latest_state[symbol]`（CurrentState）与 `last_transition[symbol]`（TransitionEvent）；首页读 CurrentState，详情/历史读 TransitionEvent；Evidence 保留到下一次真实 transition。

### 2.4 单一 30s 窗口（违反 §7）
- `main.py:46` `WINDOW_MS=30_000`；`WindowManager` 已存在却不用。
- FeatureEngine `compute_snapshot` 只接受单批 trades。
- → 必须：多时间尺度 5s/15s/30s/1m/5m（trade flow）+ 1m/5m/15m/1h（kline context），统一 WindowManager。

### 2.5 Feature 实际为 None
- `oi_change_1m/5m`、`oi_velocity`、`oi_accel`：runtime 从未 `add_oi_snapshot` → 恒 unavailable。
- `funding_percentile`、`premium_percentile`：runtime 从未 `add_funding_snapshot` → 恒 None。
- 缺：`price_return_*`、`price_accel_z`、`high_break/low_break`、`retrace_ratio`(有但未接 kline)、`acceptance`、`volume_acceleration`、`large_trade_ratio`、`average_trade_size`、`price_efficiency`、多窗口 flow。
- → 必须：FeatureSnapshot 获得真实 OI/Funding/Flow/Price/多窗口；每个 feature 可追溯 provenance。

### 2.6 硬编码 universe（违反 §9）
- `main.py:42` `SYMBOLS=["BTCUSDT","ETHUSDT","SOLUSDT"]`
- → 必须：SymbolRegistry 从 exchangeInfo 动态发现，只 ACTIVE/TRADING + USDT-M perpetual，支持 blacklist/whitelist/liquidity floor/max symbols，top-N by 24h quote volume。

### 2.7 无两阶段 Radar（违反 §10）
- runtime 对所有 symbol 跑完整 detector pipeline。
- → 必须：Stage1 LightScanner 低成本扫描全 universe → ANOMALY_CANDIDATE；Stage2 DeepScanner 仅候选跑 aggTrade flow/CVD/OI/efficiency。

### 2.8 connected≠healthy 未生效（违反 §26）
- FreshnessWatchdog 未接入；当前仅靠 `collector.stats.connected`。
- → 必须：注册每个流，`now - last_event_time > budget` → STALE → confidence UNKNOWN → 禁止 CONFIRMED。

### 2.9 其他
- `main.py:57,59` 两个 `InMemoryRepository` 实例（dashboard.repository 与 self.repository），信号历史与 latest 状态不一致。
- WS collector 未支持代理（本环境需 7890 代理）。
- `/health` 路由不存在；Dashboard 无 Radar Cards、无排序依据、无数据健康表。
- 无 `scripts/live_smoke_test.py`。
- `TestClock` 命名触发 pytest 收集告警（cosmetic）。

---

## 3. 结论

模块层实现完整、测试覆盖良好（289 passed）。**唯一致命缺陷是 runtime 编排层（`main.py`）未把已实现模块接入真实数据通路**，并存在演示覆盖与 evidence 覆盖两类 P0 bug。

改造策略遵循 AI_RULES「integrate > replace」：不重写已验收模块，而是新建 runtime 编排层把 orphan 模块接入，并补齐 Feature 多窗口/缺失项、两阶段 Radar、Dashboard、Replay 同逻辑、Smoke Test 与文档。
