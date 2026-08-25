# ARCHITECTURE

| 版本 | v1.0 |
|------|------|
| 依据 | SYSTEM_DESIGN.md §3, §13, §14 |
| 状态 | 已锁定 |

本文是 SYSTEM_DESIGN.md §3/§13/§14 的权威细化。定义分层、事件拓扑、时钟、存储与配置接口。

---

## 1. 分层架构

```
┌──────────────── Binance Futures Public Data ────────────────┐
│ aggTrade / Kline / Mark Price / OI / Funding / Premium ...  │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
            [Collectors]   只采集 + 标准化（不分析）
                       ▼
            [Normalize + Dedup + Time Alignment]
                       ▼
              [Data Health]   freshness / sequence / drift / rate-limit
                       ▼
            [Rolling Window Aggregator]
                       ▼
              [Feature Engine]   只计算（不判断）
                       ▼
   [Detectors]  anomaly / startup / false_start / continuation / withdrawal
                       ▼
              [State Machine]   转移 + guards
                       ▼
            Evidence + State + Alert  (AnalysisEvent)
                       ▼
      Dashboard / Telegram / Replay / Labeling   (只消费 AnalysisEvent)
```

**解耦铁律**：数据采集、特征计算、行为检测、状态机、展示必须解耦。任何 Detector 不能直接依赖 UI；任何 Collector 不能包含"做多/做空"逻辑。

---

## 2. 目录结构

```
src/
  domain/          # 统一事件、枚举、状态机 contracts（DATA_MODEL / STATE_MACHINE）
  collectors/      # Binance WS / REST；只采集与标准化
  health/          # freshness / sequence / drift / rate-limit
  windows/         # rolling buffers / time alignment
  features/        # volume / delta / CVD / OI / efficiency ...
  detectors/       # anomaly / startup / false_start / continuation / withdrawal
  state_machine/   # 状态转换与 transition guards
  storage/         # repository 抽象
  api/             # FastAPI endpoints / websocket to UI
  alerts/          # Telegram 等，仅消费 AnalysisEvent
  replay/          # deterministic replay / labeling
  observability/   # metrics / structured logs
  clock/           # clock abstraction（真实/测试可替换）
  config/          # 配置加载与校验
configs/
fixtures/
tests/
docs/
specs/
```

---

## 3. 事件分发拓扑

V1 采用**进程内 asyncio 单消费者 pipeline + 多订阅广播**混合模型：

```
Collectors ──► Event Bus (asyncio) ──► Data Health (过滤/标注)
                                      │
                                      ├─► Feature Engine ──► Detectors ──► State Machine ──► AnalysisEvent Bus
                                      │                                                                    │
                                      └─► (future) other consumers                                         ├─► API/WebSocket ──► UI
                                                                                                          ├─► Alerts
                                                                                                          └─► Storage/Replay
```

- **Event Bus**：进程内 `asyncio.Queue` + 按 event type 路由的多消费者分发。V1 保持简单，规模上升后迁 Redis Streams。
- **关键单向流**：Collector → Health → Feature → Detector → StateMachine。Detector 不回写 Collector；State Machine 不回写 Feature。
- **Health 标注**：事件经过 Health 层时附加/校验健康状态，Feature Engine 据此 fail closed。
- **AnalysisEvent** 是面向消费端的统一输出，UI/Alerts/Storage 只订阅它，不得订阅原始行情。

> 反模式：UI 直接订阅 TradeEvent 自己算 CVD（结果不一致、复制业务逻辑）——禁止。

---

## 4. 时钟抽象（Clock）

`src/clock/` 提供统一时钟接口，是 deterministic replay 的基础。

- `Clock.now_ms()` → 当前 UTC 毫秒。
- 实现一：`SystemClock`（wall time）。
- 实现二：`TestClock`（可控虚拟时间，replay 用）。
- 所有模块**禁止直接调用 `time.time()` / `datetime.now()`**，必须注入 Clock。
- freshness watchdog、窗口边界、状态机 hold 计时全部走 Clock。
- Replay 模式下用 TestClock 按 event_time 推进，保证可复现。

> Gate 0 即建立 clock 抽象骨架；Gate 8 replay 依赖它。

---

## 5. 存储抽象（Repository）

`src/storage/` 定义统一接口，实现可替换（V1 PostgreSQL，量大后 TimescaleDB）。

核心接口（Gate 0 建空骨架）：

| 方法 | 用途 |
|------|------|
| `save_event(event)` | 持久化原始事件子集 |
| `save_feature_snapshot(snap)` | 保存 FeatureSnapshot |
| `save_analysis_event(ev)` | 保存状态转换 + evidence/veto |
| `get_oi_snapshot_asof(symbol, target_time, tolerance)` | OI 时间对齐查询 |
| `list_transitions(symbol, since, until)` | 回放/历史查询 |

- 写入失败不得拖死 collectors：支持缓冲或降级（Gate 10 hardening）。
- Replay 读取存储事件按 event_time 重放。

---

## 6. 配置体系

`configs/` 集中管理所有阈值。分层覆盖：

```
configs/
  app.yaml              # 全局
  data_health.yaml      # freshness budget / tolerance / rate-limit
  features.yaml         # 特征窗口 / baseline / ε
  detectors.yaml        # anomaly / startup 阈值
  state_machine.yaml    # hold / cooldown / decay
  symbols.yaml          # per-symbol 覆盖（分级、过滤）
```

- 加载时校验 schema（Pydantic），非法配置 fail closed。
- V1 不要求热更新；改动需重启。
- 禁止业务代码内出现未配置化的 magic number。

---

## 7. 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 后端 | Python 3.12 + asyncio + FastAPI + Pydantic | AI 生成/审查成本低，生态完整，事件驱动 |
| 缓存/队列 | 进程内 asyncio.Queue；规模上升后 Redis Streams | V1 简单，避免分布式 |
| 数据库 | PostgreSQL；量大后 TimescaleDB | 快照/状态/事件索引/回放 |
| 前端 | React + TypeScript + Vite | 市场雷达、证据详情、健康面板 |
| 测试 | pytest + pytest-asyncio + hypothesis | 单元/异步/性质 |
| 部署 | Docker Compose + 进程管理 | 本地与服务器一致 |
| 可观测 | Prometheus-style metrics + structured logging | 监控 stale/重连/积压/429/418 |

---

## 8. 可观测性（必须 metrics）

| metric | 含义 |
|--------|------|
| `stream_age_ms{stream,symbol}` | 各流 age |
| `stream_status{stream,symbol}` | health level |
| `reconnect_count{stream}` | 重连次数 |
| `queue_depth{stage}` | 各 stage 队列积压 |
| `rest_weight_used` | REST 权重消耗 |
| `rest_429_total` / `rest_418_total` | 限频/封禁计数 |
| `state_transitions_total{from,to}` | 状态转移计数 |
| `feature_compute_lag_ms` | 特征计算延迟 |

---

## 9. 安全合规

- 默认只用公开市场数据，不要求用户 Binance API Key。
- UI 可用"主力资金/资金进入"作简化语言，但详情页必须解释为统计推断，不是识别具体账户。
- 所有提醒明确为行情分析信号，不保证收益，不自动执行交易。
- LLM 无权改变 detector 状态/阈值/证据；只读 AnalysisEvent 生成自然语言。

---

## 10. 不变量

1. 分层单向依赖：Collector → Health → Feature → Detector → StateMachine → Consumer。
2. 所有时间相关逻辑走 Clock 注入，禁止 wall time 直调。
3. 配置 schema 校验失败 ⇒ 进程 fail closed 不启动。
4. UI/Alerts 只消费 AnalysisEvent，不得订阅原始行情自行计算。
