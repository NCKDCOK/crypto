"""SQLite 本地持久化 Repository — V1.2 停机恢复基础层。

依据：用户追加需求 + ARCHITECTURE.md §5（V1 PostgreSQL，量大后 TimescaleDB）
本地 SQLite 持久化：K 线（closed bar）、OI 快照、Funding 快照、AnalysisEvent（信号）、
Trade Plan 快照。程序关闭不丢；重启读取最后写入时间判断停机时长，按三档策略恢复。

写入失败不得拖死 collectors（best-effort，异常仅记录日志）。
线程安全：单一连接 + Lock（runtime 单事件循环，访问轻量）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.domain import (
    AnalysisEvent,
    Direction,
    Evidence,
    EvidenceFamily,
    FeatureSnapshot,
    FundingRateSnapshot,
    KlineEvent,
    KlineInterval,
    OpenInterestSnapshot,
    State,
    Veto,
    VetoSeverity,
    VetoType,
)
from src.storage import InMemoryRepository, Repository

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    close_time INTEGER NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume TEXT NOT NULL,
    quote_volume TEXT,
    trade_count INTEGER NOT NULL,
    is_closed INTEGER NOT NULL,
    receive_time INTEGER NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
);
CREATE INDEX IF NOT EXISTS idx_klines_time ON klines(symbol, interval, open_time);

CREATE TABLE IF NOT EXISTS oi_snapshots (
    symbol TEXT NOT NULL,
    event_time INTEGER NOT NULL,
    receive_time INTEGER NOT NULL,
    open_interest TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, event_time)
);
CREATE INDEX IF NOT EXISTS idx_oi_time ON oi_snapshots(symbol, receive_time);

CREATE TABLE IF NOT EXISTS funding_snapshots (
    symbol TEXT NOT NULL,
    event_time INTEGER NOT NULL,
    receive_time INTEGER NOT NULL,
    mark_price TEXT NOT NULL,
    index_price TEXT NOT NULL,
    last_funding_rate TEXT NOT NULL,
    next_funding_time INTEGER NOT NULL,
    premium TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, event_time)
);

CREATE TABLE IF NOT EXISTS analysis_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    asof INTEGER NOT NULL,
    previous_state TEXT NOT NULL,
    new_state TEXT NOT NULL,
    direction TEXT,
    confidence_state TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    vetoes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ae_symbol_time ON analysis_events(symbol, asof);

CREATE TABLE IF NOT EXISTS trade_plan_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    created_asof INTEGER NOT NULL,
    expired INTEGER NOT NULL DEFAULT 0,
    plan_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tp_symbol ON trade_plan_snapshots(symbol, expired);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── V1.3 模拟验证（§59 持久化表）──

CREATE TABLE IF NOT EXISTS recommendation_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rs_symbol_time ON recommendation_snapshots(symbol, created_at);

CREATE TABLE IF NOT EXISTS simulation_queue (
    simulation_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    item_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sq_status ON simulation_queue(status, updated_at);

CREATE TABLE IF NOT EXISTS simulation_positions (
    simulation_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    position_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS simulation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asof INTEGER NOT NULL,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_se_sim_time ON simulation_events(simulation_id, asof);

CREATE TABLE IF NOT EXISTS simulation_results (
    simulation_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sr_symbol ON simulation_results(symbol);
"""


class SqliteRepository(Repository):
    """SQLite 本地持久化实现。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── K 线 ──

    def save_kline(self, kline: KlineEvent) -> None:
        if not kline.is_closed:
            return  # 仅持久化 closed bar，避免未收盘 bar 污染历史
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO klines
                    (symbol, interval, open_time, close_time, open, high, low, close,
                     volume, quote_volume, trade_count, is_closed, receive_time)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        kline.symbol, kline.interval.value, kline.open_time, kline.close_time,
                        str(kline.open), str(kline.high), str(kline.low), str(kline.close),
                        str(kline.volume), str(kline.quote_volume) if kline.quote_volume is not None else None,
                        kline.trade_count, int(kline.is_closed), kline.receive_time,
                    ),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_kline_failed symbol=%s", kline.symbol)

    def get_recent_klines(
        self, symbol: str, interval: str, limit: int = 300,
    ) -> list[KlineEvent]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    """SELECT * FROM klines WHERE symbol=? AND interval=?
                    ORDER BY open_time DESC LIMIT ?""",
                    (symbol, interval, limit),
                ).fetchall()
        except Exception:
            logger.exception("sqlite_get_recent_klines_failed")
            return []
        rows = list(reversed(rows))  # 时间正序
        return [_row_to_kline(r) for r in rows]

    def get_klines_since(
        self, symbol: str, interval: str, since_open_time: int,
    ) -> list[KlineEvent]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    """SELECT * FROM klines WHERE symbol=? AND interval=? AND open_time>=?
                    ORDER BY open_time ASC""",
                    (symbol, interval, since_open_time),
                ).fetchall()
        except Exception:
            logger.exception("sqlite_get_klines_since_failed")
            return []
        return [_row_to_kline(r) for r in rows]

    # ── OI ──

    def save_oi_snapshot(self, snap: OpenInterestSnapshot) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO oi_snapshots
                    (symbol, event_time, receive_time, open_interest, source)
                    VALUES (?,?,?,?,?)""",
                    (snap.symbol, snap.event_time, snap.receive_time,
                     str(snap.open_interest), snap.source),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_oi_failed symbol=%s", snap.symbol)

    def get_recent_oi(self, symbol: str, limit: int = 200) -> list[OpenInterestSnapshot]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    """SELECT * FROM oi_snapshots WHERE symbol=?
                    ORDER BY event_time DESC LIMIT ?""",
                    (symbol, limit),
                ).fetchall()
        except Exception:
            logger.exception("sqlite_get_recent_oi_failed")
            return []
        rows = list(reversed(rows))
        return [_row_to_oi(r) for r in rows]

    # ── Funding ──

    def save_funding_snapshot(self, snap: FundingRateSnapshot) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO funding_snapshots
                    (symbol, event_time, receive_time, mark_price, index_price,
                     last_funding_rate, next_funding_time, premium, source)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (snap.symbol, snap.event_time, snap.receive_time,
                     str(snap.mark_price), str(snap.index_price), str(snap.last_funding_rate),
                     snap.next_funding_time, str(snap.premium), snap.source),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_funding_failed symbol=%s", snap.symbol)

    # ── AnalysisEvent（信号）──

    def save_analysis_event(self, ev: AnalysisEvent) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO analysis_events
                    (symbol, asof, previous_state, new_state, direction, confidence_state,
                     evidence_json, vetoes_json)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        ev.symbol, ev.asof, ev.previous_state.value, ev.new_state.value,
                        ev.direction.value if ev.direction else None, ev.confidence_state.value,
                        json.dumps([_evidence_to_dict(e) for e in ev.evidence]),
                        json.dumps([_veto_to_dict(v) for v in ev.vetoes]),
                    ),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_analysis_event_failed symbol=%s", ev.symbol)

    def list_transitions(
        self, symbol: str, since: int, until: int,
    ) -> list[AnalysisEvent]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    """SELECT * FROM analysis_events WHERE symbol=? AND asof>=? AND asof<=?
                    ORDER BY asof ASC""",
                    (symbol, since, until),
                ).fetchall()
        except Exception:
            logger.exception("sqlite_list_transitions_failed")
            return []
        return [_row_to_analysis_event(r) for r in rows]

    # ── Trade Plan ──

    def save_trade_plan(self, symbol: str, asof: int, plan: dict[str, Any]) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO trade_plan_snapshots (symbol, created_asof, expired, plan_json)
                    VALUES (?,?,0,?)""",
                    (symbol, asof, json.dumps(plan, ensure_ascii=False)),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_trade_plan_failed symbol=%s", symbol)

    def expire_trade_plans(self, symbol: str | None, before_asof: int) -> int:
        """将 before_asof 之前创建的未过期 Trade Plan 标记为 EXPIRED。返回标记数量。"""
        try:
            with self._lock:
                if symbol is None:
                    cur = self._conn.execute(
                        "UPDATE trade_plan_snapshots SET expired=1 WHERE expired=0 AND created_asof<?",
                        (before_asof,),
                    )
                else:
                    cur = self._conn.execute(
                        "UPDATE trade_plan_snapshots SET expired=1 WHERE expired=0 AND symbol=? AND created_asof<?",
                        (symbol, before_asof),
                    )
                self._conn.commit()
                return cur.rowcount or 0
        except Exception:
            logger.exception("sqlite_expire_trade_plans_failed")
            return 0

    def get_active_trade_plan(self, symbol: str) -> dict[str, Any] | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    """SELECT * FROM trade_plan_snapshots WHERE symbol=? AND expired=0
                    ORDER BY created_asof DESC LIMIT 1""",
                    (symbol,),
                ).fetchone()
        except Exception:
            logger.exception("sqlite_get_active_trade_plan_failed")
            return None
        if row is None:
            return None
        return json.loads(row["plan_json"])

    # ── V1.3 模拟验证持久化（§59）──

    def save_recommendation_snapshot(self, symbol: str, asof: int, snap: dict[str, Any]) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO recommendation_snapshots (snapshot_id, symbol, created_at, snapshot_json)
                    VALUES (?,?,?,?)""",
                    (snap["snapshot_id"], symbol, asof, json.dumps(snap, ensure_ascii=False)),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_recommendation_snapshot_failed symbol=%s", symbol)

    def list_recommendation_snapshots(self, symbol: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        try:
            with self._lock:
                if symbol is None:
                    rows = self._conn.execute(
                        "SELECT * FROM recommendation_snapshots ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT * FROM recommendation_snapshots WHERE symbol=? ORDER BY created_at DESC LIMIT ?",
                        (symbol, limit),
                    ).fetchall()
        except Exception:
            logger.exception("sqlite_list_recommendation_snapshots_failed")
            return []
        return [json.loads(r["snapshot_json"]) for r in rows]

    def save_simulation_queue_item(self, item: dict[str, Any]) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO simulation_queue (simulation_id, snapshot_id, symbol, status, updated_at, item_json)
                    VALUES (?,?,?,?,?,?)""",
                    (item["simulation_id"], item["snapshot_id"], item["symbol"], item["status"],
                     item["updated_at"], json.dumps(item, ensure_ascii=False)),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_simulation_queue_item_failed id=%s", item.get("simulation_id"))

    def list_simulation_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        try:
            with self._lock:
                if status is None:
                    rows = self._conn.execute(
                        "SELECT * FROM simulation_queue ORDER BY updated_at DESC"
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT * FROM simulation_queue WHERE status=? ORDER BY updated_at DESC",
                        (status,),
                    ).fetchall()
        except Exception:
            logger.exception("sqlite_list_simulation_queue_failed")
            return []
        return [json.loads(r["item_json"]) for r in rows]

    def get_simulation_queue_item(self, simulation_id: str) -> dict[str, Any] | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM simulation_queue WHERE simulation_id=?", (simulation_id,),
                ).fetchone()
        except Exception:
            logger.exception("sqlite_get_simulation_queue_item_failed id=%s", simulation_id)
            return None
        return json.loads(row["item_json"]) if row else None

    def save_simulation_position(self, pos: dict[str, Any]) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO simulation_positions (simulation_id, symbol, status, position_json)
                    VALUES (?,?,?,?)""",
                    (pos["simulation_id"], pos["symbol"], pos["status"], json.dumps(pos, ensure_ascii=False)),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_simulation_position_failed id=%s", pos.get("simulation_id"))

    def list_simulation_positions(self, status: str | None = None) -> list[dict[str, Any]]:
        try:
            with self._lock:
                if status is None:
                    rows = self._conn.execute(
                        "SELECT * FROM simulation_positions ORDER BY simulation_id"
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT * FROM simulation_positions WHERE status=? ORDER BY simulation_id",
                        (status,),
                    ).fetchall()
        except Exception:
            logger.exception("sqlite_list_simulation_positions_failed")
            return []
        return [json.loads(r["position_json"]) for r in rows]

    def get_simulation_position(self, simulation_id: str) -> dict[str, Any] | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM simulation_positions WHERE simulation_id=?", (simulation_id,),
                ).fetchone()
        except Exception:
            logger.exception("sqlite_get_simulation_position_failed id=%s", simulation_id)
            return None
        return json.loads(row["position_json"]) if row else None

    def save_simulation_event(
        self, simulation_id: str, symbol: str, asof: int,
        old_status: str, new_status: str, reason: str | None,
    ) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO simulation_events (simulation_id, symbol, asof, old_status, new_status, reason)
                    VALUES (?,?,?,?,?,?)""",
                    (simulation_id, symbol, asof, old_status, new_status, reason),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_simulation_event_failed id=%s", simulation_id)

    def list_simulation_events(self, simulation_id: str, limit: int = 200) -> list[dict[str, Any]]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM simulation_events WHERE simulation_id=? ORDER BY asof ASC LIMIT ?",
                    (simulation_id, limit),
                ).fetchall()
        except Exception:
            logger.exception("sqlite_list_simulation_events_failed id=%s", simulation_id)
            return []
        return [
            {
                "simulation_id": r["simulation_id"], "symbol": r["symbol"], "asof": r["asof"],
                "old_status": r["old_status"], "new_status": r["new_status"], "reason": r["reason"],
            }
            for r in rows
        ]

    def save_simulation_result(self, result: dict[str, Any]) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO simulation_results (simulation_id, snapshot_id, symbol, result_json)
                    VALUES (?,?,?,?)""",
                    (result["simulation_id"], result["snapshot_id"], result["symbol"],
                     json.dumps(result, ensure_ascii=False)),
                )
                self._conn.commit()
        except Exception:
            logger.exception("sqlite_save_simulation_result_failed id=%s", result.get("simulation_id"))

    def list_simulation_results(self, limit: int = 500) -> list[dict[str, Any]]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM simulation_results ORDER BY simulation_id DESC LIMIT ?", (limit,),
                ).fetchall()
        except Exception:
            logger.exception("sqlite_list_simulation_results_failed")
            return []
        return [json.loads(r["result_json"]) for r in rows]

    def get_simulation_result(self, simulation_id: str) -> dict[str, Any] | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM simulation_results WHERE simulation_id=?", (simulation_id,),
                ).fetchone()
        except Exception:
            logger.exception("sqlite_get_simulation_result_failed id=%s", simulation_id)
            return None
        return json.loads(row["result_json"]) if row else None

    # ── 恢复查询 ──

    def get_last_write_ms(self) -> int | None:
        """最后写入时间 = 各表 receive_time/asof 最大值。无数据返回 None（首次启动）。"""
        queries = [
            "SELECT MAX(receive_time) AS m FROM klines",
            "SELECT MAX(receive_time) AS m FROM oi_snapshots",
            "SELECT MAX(receive_time) AS m FROM funding_snapshots",
            "SELECT MAX(asof) AS m FROM analysis_events",
        ]
        try:
            with self._lock:
                vals = []
                for q in queries:
                    row = self._conn.execute(q).fetchone()
                    m = row["m"] if row else None
                    if m is not None:
                        vals.append(int(m))
        except Exception:
            logger.exception("sqlite_get_last_write_ms_failed")
            return None
        return max(vals) if vals else None

    def get_latest_kline_open_time(self, symbol: str, interval: str) -> int | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT MAX(open_time) AS m FROM klines WHERE symbol=? AND interval=?",
                    (symbol, interval),
                ).fetchone()
        except Exception:
            return None
        return int(row["m"]) if row and row["m"] is not None else None

    # ── Repository 接口（兼容）──

    async def save_event(self, event: Any) -> None:
        if isinstance(event, KlineEvent):
            self.save_kline(event)
        elif isinstance(event, OpenInterestSnapshot):
            self.save_oi_snapshot(event)
        elif isinstance(event, FundingRateSnapshot):
            self.save_funding_snapshot(event)

    async def save_feature_snapshot(self, snap: FeatureSnapshot) -> None:
        pass  # FeatureSnapshot 不持久化（量大；按需在 P23 Replay 单独记录）

    async def get_oi_snapshot_asof(
        self, symbol: str, target_time: int, tolerance: int,
    ) -> OpenInterestSnapshot | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    """SELECT * FROM oi_snapshots WHERE symbol=?
                    AND ABS(receive_time - ?) <= ?
                    ORDER BY ABS(receive_time - ?) ASC LIMIT 1""",
                    (symbol, target_time, tolerance, target_time),
                ).fetchone()
        except Exception:
            return None
        return _row_to_oi(row) if row else None


# ── 行 ↔ 对象 转换 ──


def _row_to_kline(r: sqlite3.Row) -> KlineEvent:
    return KlineEvent(
        symbol=r["symbol"],
        interval=KlineInterval(r["interval"]),
        open_time=r["open_time"],
        close_time=r["close_time"],
        event_time=r["open_time"],
        receive_time=r["receive_time"],
        open=Decimal(r["open"]),
        high=Decimal(r["high"]),
        low=Decimal(r["low"]),
        close=Decimal(r["close"]),
        volume=Decimal(r["volume"]),
        quote_volume=Decimal(r["quote_volume"]) if r["quote_volume"] is not None else None,
        trade_count=r["trade_count"],
        is_closed=bool(r["is_closed"]),
    )


def _row_to_oi(r: sqlite3.Row) -> OpenInterestSnapshot:
    return OpenInterestSnapshot(
        symbol=r["symbol"],
        event_time=r["event_time"],
        receive_time=r["receive_time"],
        open_interest=Decimal(r["open_interest"]),
        source=r["source"],
        freshness_ms=0,
    )


def _row_to_analysis_event(r: sqlite3.Row) -> AnalysisEvent:
    ev_list = json.loads(r["evidence_json"]) if r["evidence_json"] else []
    veto_list = json.loads(r["vetoes_json"]) if r["vetoes_json"] else []
    return AnalysisEvent(
        symbol=r["symbol"],
        direction=Direction(r["direction"]) if r["direction"] else None,
        previous_state=State(r["previous_state"]),
        new_state=State(r["new_state"]),
        evidence=[_dict_to_evidence(e) for e in ev_list],
        vetoes=[_dict_to_veto(v) for v in veto_list],
        asof=r["asof"],
        confidence_state=__import__("src.domain", fromlist=["ConfidenceState"]).ConfidenceState(
            r["confidence_state"]
        ),
    )


def _evidence_to_dict(e: Evidence) -> dict[str, Any]:
    return {
        "family": e.family.value, "type": e.type, "window": e.window,
        "value": e.value, "threshold": e.threshold, "passed": e.passed, "source": e.source,
    }


def _dict_to_evidence(d: dict[str, Any]) -> Evidence:
    return Evidence(
        family=EvidenceFamily(d["family"]), type=d["type"], window=d.get("window"),
        value=d.get("value"), threshold=d.get("threshold"),
        passed=d.get("passed", False), source=d.get("source"),
    )


def _veto_to_dict(v: Veto) -> dict[str, Any]:
    return {"type": v.type.value, "triggered": v.triggered,
            "severity": v.severity.value, "detail": v.detail}


def _dict_to_veto(d: dict[str, Any]) -> Veto:
    return Veto(
        type=VetoType(d["type"]), triggered=d.get("triggered", False),
        severity=VetoSeverity(d.get("severity", "soft")), detail=d.get("detail"),
    )
