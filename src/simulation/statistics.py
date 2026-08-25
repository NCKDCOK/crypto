"""模拟统计 — §37 / §38 / §39 汇总与分桶。

依据：V1.3 更新计划 §37 / §38 / §39 / §72 闭环。

- §37：至少 推荐次数 / 进入观察区次数 / 通过 Revalidation 次数 / 模拟入场次数 /
  TP1 / TP2 / 失效次数 / 撤离退出次数 / 平均 MFE / 平均 MAE。
- §38：分桶统计 — Opportunity Score / Signal Confirmation / Setup Type /
  Market Regime / Direction / Timeframe。
- §39：Setup 统计 — 回踩复燃、疑似吸筹 → 启动转化率。
"""

from __future__ import annotations

from typing import Any


def _band(value: float | None, edges: list[float]) -> str:
    """把分值映射到区间标签，如 [70, 80) → '70-79'。"""
    if value is None:
        return "unknown"
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return f"{int(edges[i])}-{int(edges[i + 1]) - 1}"
    if value >= edges[-1]:
        return f">={int(edges[-1])}"
    return "<" + str(int(edges[0]))


class SimulationStatistics:
    """§37–§39 统计计算（纯函数风格，数据由 runtime/API 传入）。"""

    @staticmethod
    def compute(
        recommendations: list[dict[str, Any]],
        queue_items: list[dict[str, Any]],
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snap_by_id = {r.get("snapshot_id"): r for r in recommendations}

        # ── §37 总览 ──
        entered = [i for i in queue_items if i.get("entered_at") is not None]
        zone_reached = [
            i for i in queue_items
            if i.get("entry_zone_reached_at") is not None or i.get("armed_at") is not None
        ]
        reval_passed = [i for i in queue_items
                        if i.get("revalidate_result", {}).get("passed") or i.get("armed_at") is not None]
        closed_results = [r for r in results if r.get("exit_reason")]

        def _count(pred) -> int:
            return sum(1 for r in closed_results if pred(r))

        avg_mfe = sum(r.get("mfe_pct") or 0.0 for r in closed_results) / len(closed_results) if closed_results else 0.0
        avg_mae = sum(r.get("mae_pct") or 0.0 for r in closed_results) / len(closed_results) if closed_results else 0.0

        overview = {
            "recommendations": len(recommendations),            # 推荐次数
            "zone_reached": len(zone_reached),                  # 进入观察区次数
            "revalidation_passed": len(reval_passed),           # 通过 Revalidation 次数
            "entries": len(entered),                            # 模拟入场次数
            "tp1_hit": _count(lambda r: r.get("tp1_hit")),
            "tp2_hit": _count(lambda r: r.get("tp2_hit")),
            "tp3_hit": _count(lambda r: r.get("tp3_hit")),
            "invalidation": _count(lambda r: r.get("invalidation_hit")),
            "withdrawal_exit": _count(lambda r: r.get("exit_reason") == "SIGNAL_WITHDRAWAL"),
            "distribution_exit": _count(lambda r: r.get("exit_reason") == "DISTRIBUTION_EXIT"),
            "direction_flip": _count(lambda r: r.get("exit_reason") == "DIRECTION_FLIP"),
            "time_expired": _count(lambda r: r.get("exit_reason") == "TIME_EXPIRED"),
            "closed": len(closed_results),
            "avg_mfe_pct": round(avg_mfe, 4),
            "avg_mae_pct": round(avg_mae, 4),
        }

        # ── §38 分桶（以推荐快照字段为准）──
        def _buckets(field: str, edges: list[float] | None = None) -> dict[str, Any]:
            buckets: dict[str, dict[str, Any]] = {}
            for r in closed_results:
                snap = snap_by_id.get(r.get("snapshot_id")) or {}
                raw = snap.get(field)
                key = _band(raw, edges) if edges is not None else (raw or "unknown")
                b = buckets.setdefault(str(key), {"count": 0, "pnl_sum": 0.0})
                b["count"] += 1
                b["pnl_sum"] += r.get("pnl_pct") or 0.0
            return {
                k: {"count": v["count"], "avg_pnl_pct": round(v["pnl_sum"] / v["count"], 4) if v["count"] else 0.0}
                for k, v in sorted(buckets.items())
            }

        # 累计收益率（按符号求和，便于比较 setup 优劣）
        def _cum(field: str, edges: list[float] | None = None) -> dict[str, Any]:
            buckets: dict[str, dict[str, Any]] = {}
            for r in closed_results:
                snap = snap_by_id.get(r.get("snapshot_id")) or {}
                raw = snap.get(field)
                key = _band(raw, edges) if edges is not None else (raw or "unknown")
                b = buckets.setdefault(str(key), {"count": 0, "pnl_sum": 0.0})
                b["count"] += 1
                b["pnl_sum"] += r.get("pnl_pct") or 0.0
            return {
                k: {"count": v["count"], "cum_pnl_pct": round(v["pnl_sum"], 4)}
                for k, v in sorted(buckets.items())
            }

        buckets = {
            "opportunity_score": _buckets("opportunity_score", [0, 70, 80, 90]),
            "signal_confirmation": _buckets("signal_confirmation", [0, 75, 85]),
            "setup_type": _cum("setup_type"),
            "direction": _cum("direction"),
            "timeframe": _cum("primary_timeframe"),
        }

        # §38 market_regime 取快照 market_regime dict 里的 regime 字段
        regime_buckets: dict[str, dict[str, Any]] = {}
        for r in closed_results:
            snap = snap_by_id.get(r.get("snapshot_id")) or {}
            rg = snap.get("market_regime") or {}
            key = rg.get("regime") or "unknown"
            b = regime_buckets.setdefault(str(key), {"count": 0, "pnl_sum": 0.0})
            b["count"] += 1
            b["pnl_sum"] += r.get("pnl_pct") or 0.0
        buckets["market_regime"] = {
            k: {"count": v["count"], "cum_pnl_pct": round(v["pnl_sum"], 4)}
            for k, v in sorted(regime_buckets.items())
        }

        # ── §39 Setup 转化率 ──
        setup_conversion: dict[str, Any] = {}
        for snap in recommendations:
            key = snap.get("setup_type") or "unknown"
            entry = setup_conversion.setdefault(key, {"recommended": 0, "entered": 0})
            entry["recommended"] += 1
        for r in results:
            snap = snap_by_id.get(r.get("snapshot_id")) or {}
            key = snap.get("setup_type") or "unknown"
            entry = setup_conversion.setdefault(key, {"recommended": 0, "entered": 0})
            entry["entered"] += 1
        for k, v in setup_conversion.items():
            v["conversion_rate"] = round(
                v["entered"] / v["recommended"], 4) if v["recommended"] else 0.0

        return {
            "overview": overview,
            "buckets": buckets,
            "setup_conversion": setup_conversion,
        }