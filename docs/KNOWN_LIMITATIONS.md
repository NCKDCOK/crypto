# KNOWN_LIMITATIONS — 已知限制

| 版本 | v1.0 |
|------|------|
| 依据 | 改造任务文档 §34-§35 |

## 1. 本轮已实现（V1 Runtime 真实性）

- 动态 universe（SymbolRegistry + 24h ticker top-N，blacklist/whitelist/liquidity floor/max_symbols）
- AggTrade / Kline / OI / Funding 全部 live
- Data Health 真实工作（FreshnessWatchdog + ConfidenceTracker，connected≠healthy，fail closed）
- 无强制 CONFIDENT（confidence 由 health 派生）
- Evidence 不被 dashboard refresh 擦除（latest_state / last_evidence_transition 分离）
- 多时间窗口（5s/15s/30s/1m/5m + kline 1m/5m/15m/1h）
- FeatureSnapshot 获得真实 OI/Funding/Flow/Price/多窗口 + price_efficiency
- 全部 Detector 使用真实数据；8 个 FalseStart Veto（含 ONE_BAR_SPIKE）
- 两阶段 Radar（LightScanner + DeepScanner）
- Dashboard：Radar Cards + /health + 详情 Evidence/Veto + 排序
- Replay 同一套核心逻辑（health-driven confidence，deterministic，多流）
- Live smoke test（BTC/ETH/SOL，PASS/FAIL）

## 2. 已知限制 / 风险

1. **冷启动 baseline 不稳定**：robust z-score 需 ≥3 baseline 样本。runtime 启动初期（前 ~30s）z-score 波动大，可能产生早期 anomaly → REJECTED（fail closed，安全但噪声）。基线稳定后趋于正常。
2. **Stage1 LightScanner 用 24h ticker**：相对 volume / trade count 用跨扫描 baseline 近似，非逐 kline 精确 RVOL。候选召回以"24h 异动 + 跨扫描 z"为主，后续可引入近期 kline 精确化。
3. **DeepScanner WS 重建**：候选集变化时重建 WS 连接（set 变化才重建，已防抖动）。重建瞬间该批 symbol 短暂 STALE → confidence UNKNOWN（fail closed）。
4. **OI as-of 容差**：5s 轮询下 1m/5m/15m change 在容差内取最近快照；冷启动 5m change 需积累 ≥5m 数据才可用。
5. **无 Depth/OFI**：P1 depth/bookticker 未实现（§35 暂禁），吸收判断仅靠 flow_impact / price_efficiency。
6. **无 Liquidation / TopTrader / TakerBuySellRatio**：P2 数据源未接入。
7. **存储为 InMemory**：V1 用 InMemoryRepository；PostgreSQL/TimescaleDB 未接入（ARCHITECTURE §5 预留接口）。重启不保留历史。
8. **地理限制**：本机需经 `proxy` 访问 Binance；生产环境直连需确认网络可达。
9. **Replay `simulate_healthy_streams`**：纯 trades replay 默认 confidence=UNKNOWN（关键流 kline/OI 未 replay）。如需在 replay 中验证完整确认链，需提供多流输入或开启 simulate_healthy_streams（仅 replay，已记录于 provenance）。
10. **无机会分**：V1 只输出 Evidence + State + Veto，无 0-100 评分（§20/§35，评分延后 Gate 8 历史样本校准）。

## 3. 本轮明确不做（§35）

SMC / Order Block / FVG / Fibonacci / AI 自动评分 / 机器学习 / Q-learning / 神经网络 / 自动参数优化 / 自动交易 / 用户账户系统 / 多交易所 / 花哨图表。
