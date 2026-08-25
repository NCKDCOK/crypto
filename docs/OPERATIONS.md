# OPERATIONS — 运维手册

| 版本 | v1.0 |
|------|------|
| 依据 | 改造任务文档 §25-§29/§32 |

## 1. 启动

```bash
# 安装依赖
pip install -e .   # 或 pip install -r requirements（见 pyproject.toml）

# 启动 Dashboard（默认 127.0.0.1:8050）
uvicorn src.main:app --host 127.0.0.1 --port 8050
```

浏览器访问 `http://127.0.0.1:8050/`。

## 2. 代理（受限网络环境）

`configs/app.yaml` 的 `proxy` 字段。WS 与 REST 均走该代理。生产环境留空则直连。

```yaml
proxy: "http://127.0.0.1:7890"   # 本机受限用；生产留空
```

## 3. 配置要点（configs/）

| 文件 | 关键项 |
|---|---|
| app.yaml | proxy / light_scan_interval_s / deep_compute_interval_s / deep_max_symbols |
| symbols.yaml | top_n / max_symbols / liquidity_floor_usdt / whitelist / exclude_patterns |
| data_health.yaml | freshness budget / oi_poll_interval_s / funding_poll_interval_s / rate_limiter |
| features.yaml | trade_flow_windows / kline_context_intervals / epsilon / baseline_max_samples |
| detectors.yaml | anomaly / startup / veto / continuation/withdrawal / light scanner 阈值 |
| state_machine.yaml | hold / cooldown / decay 窗口 |

## 4. Live Smoke Test（§28）

```bash
# 默认 10 分钟，BTC/ETH/SOL
python scripts/live_smoke_test.py --duration 600 --proxy http://127.0.0.1:7890
```

输出 trade count / duplicate count / 各流 age / reconnect / queue lag p50/p95 / feature sample / transitions，打印 `RESULT: PASS` / `RESULT: FAIL`。
退出码 0=PASS，1=FAIL。

## 5. 测试

```bash
pytest -q                       # 全量（离线，不连 Binance）
pytest tests/test_v1_integration.py   # V1 改造新增
```

## 6. Dashboard 路由

| 路由 | 内容 |
|---|---|
| `/` | Market Radar 卡片 + 信号历史 |
| `/api/radar` | 各 symbol CurrentState（state/direction/confidence/evidence/veto 摘要） |
| `/api/health` | 数据健康表（每流 status/age/connected + confidence） |
| `/api/symbol/{symbol}` | 详情：features / health / 完整 Evidence / Veto / state timeline |
| `/api/signals` | transition 历史 |
| `/api/stats` | universe/deep/candidate 数 + 状态计数 + rate_limiter + queue |

## 7. 排序依据（§23）

状态优先级：START_CONFIRMED > CONTINUATION > SUSPECTED_START > ANOMALY > EXHAUSTION > WITHDRAWAL > REJECTED/COOLDOWN > SLEEPING。
同状态内：evidence count ↓ → anomaly strength ↓ → recency ↓。不使用神秘评分。

## 8. 性能观察（§29）

- event/sec / queue size / queue lag / symbol count / REST request count / reconnect count
- bounded queue（trade maxsize=50000）；消费者落后 → Health 降级，禁止无限积压。
- deep_max_symbols 控制 WS 压力（单连接 ≤1024 streams）。

## 9. 429/418 防护（§25）

所有 REST 经统一 RateLimiter：权重预算（每分钟滚动重置）+ Retry-After + 指数退避 + 熔断（连续 3 次 429 → circuit open）+ 418 IP 封禁长等待。REST 失败返回 unavailable/stale，不用假默认数据。
