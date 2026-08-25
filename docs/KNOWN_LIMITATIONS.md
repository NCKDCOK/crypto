# KNOWN_LIMITATIONS — 已知限制

| 版本 | v1.1 |
|------|------|
| 依据 | V1.1 计划 §三十七/§三十九 |

## 1. V1.1 已实现

- V1.0 全部功能保留
- **P0 修复**：Stage1 增量异动、多周期 Kline（1m/5m/15m/1h）、候选防抖（增量 subscribe/unsubscribe + 驻留 180s）、数据状态翻译、Dashboard 统一
- **评分引擎**：11 个子评分 + OpportunityScore + 可展开 breakdown + 配置化权重
- **置信度引擎**：独立数值置信度，受数据健康/证据完整性/多窗口一致性影响
- **翻译层**：PresentationTranslator 内部术语 → 用户中文
- **Top10 排名**：RankingScore = OpportunityScore × ConfidenceFactor
- **UI 大屏**：6 页面 SPA（首页/全市场/详情/信号/健康/回放），科技感深色主题
- **评分预热**：样本不足时不评分
- **极端值保护**：`_sigmoid_z` 限制指数参数范围，防止溢出

## 2. 已知限制 / 风险

1. **冷启动 baseline 不稳定**：robust z-score 需 ≥3 baseline 样本。runtime 启动初期 z-score 波动大。评分预热机制（`warmup_min_samples`）可缓解。
2. **Stage1 用 24h ticker 增量**：V1.1 改为短时增量（ΔQuoteVolume/ΔTradeCount/ΔPrice），但仍基于 24h ticker 采样，非逐 kline 精确 RVOL。
3. **WS 增量订阅依赖连接已建立**：首次连接仍通过 URL 全量订阅；增量 subscribe/unsubscribe 仅在连接存活期间生效。重连时用更新后的 config.streams 重建 URL。
4. **OI as-of 容差**：5s 轮询下 1m/5m/15m change 需积累数据。冷启动 OI 可能 STALE。
5. **无 Depth/OFI**：P1 depth/bookticker 未实现（§37 暂禁）。
6. **无 Liquidation / TopTrader**：P2 数据源未接入。
7. **存储为 InMemory**：重启不保留历史。PostgreSQL/TimescaleDB 未接入。
8. **地理限制**：本机需经 `proxy` 访问 Binance；生产直连需确认网络可达。
9. **评分权重为初始值**：需 Replay 数据校准后调整（计划 §十）。
10. **回放验证页为占位**：需积累运行数据后实现 Replay 统计 API。
11. **旧 DashboardService 未删除**：标记 DEPRECATED 保留参考。
12. **无 SSR**：纯客户端渲染，SEO 不友好（内部工具，无需 SEO）。

## 3. 本轮明确不做（§三十七）

SMC / Order Block / FVG / Fibonacci / AI 自动评分 / 机器学习 / Q-learning / 神经网络 / 自动参数优化 / 自动交易 / 用户账户系统 / 多交易所 / 花哨图表。
