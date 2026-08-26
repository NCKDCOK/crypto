"""PublishedRecommendationRepository — 正式推荐存储（V1.4 §一.2）。

首页主要读取此 Repository（而非实时排行榜，§十.2）。

- 引擎侧：内存 dict（活跃 + 终态保留），发布 / 更新 / 退出即时生效。
- 持久化：委托 SqliteRepository（表 published_recommendations），重启恢复（§二十六）。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from src.recommendations.models import (
    ACTIVE_STATUSES,
    PublishedRecommendation,
    RecommendationStatus,
)

logger = logging.getLogger(__name__)


class PublishedRecommendationRepository:
    """已发布推荐仓库。"""

    def __init__(self, storage: Any | None = None) -> None:
        # storage: SqliteRepository（或其测试替身，须实现 save_published_recommendation /
        # list_published_recommendations / get_published_recommendation）
        self.storage = storage
        self._recs: dict[str, PublishedRecommendation] = {}

    # ── 写 ──

    def save(self, rec: PublishedRecommendation) -> None:
        """保存/更新一条推荐（内存 + 持久化）。"""
        self._recs[rec.recommendation_id] = rec
        if self.storage is not None:
            try:
                self.storage.save_published_recommendation(rec.to_dict())
            except Exception:
                logger.exception("persist_published_recommendation_failed id=%s", rec.recommendation_id)

    def upsert_dict(self, rec_dict: dict[str, Any]) -> PublishedRecommendation:
        rec = PublishedRecommendation.from_dict(rec_dict)
        self.save(rec)
        return rec

    # ── 读 ──

    def get(self, recommendation_id: str) -> PublishedRecommendation | None:
        return self._recs.get(recommendation_id)

    def active(self, limit: int = 50) -> list[PublishedRecommendation]:
        """首页活跃区（PUBLISHED / MONITORING / WEAKENING / RISK），发布时间降序。"""
        recs = [r for r in self._recs.values() if r.is_active()]
        recs.sort(key=lambda r: r.published_at, reverse=True)
        return recs[:limit]

    def active_by_symbol(self, symbol: str) -> PublishedRecommendation | None:
        """该 symbol 当前活跃推荐（同 symbol 只允许一条活跃，§三十四 冷却/去重）。"""
        for r in self.active():
            if r.symbol == symbol:
                return r
        return None

    def all(self) -> list[PublishedRecommendation]:
        return list(self._recs.values())

    def by_symbol(self, symbol: str) -> list[PublishedRecommendation]:
        return [r for r in self._recs.values() if r.symbol == symbol]

    def list_recent(self, limit: int = 100) -> list[PublishedRecommendation]:
        recs = list(self._recs.values())
        recs.sort(key=lambda r: r.published_at, reverse=True)
        return recs[:limit]

    # ── 重启恢复（§二十六）──

    def restore(self) -> None:
        """从持久层恢复全部推荐（含终态）。"""
        if self.storage is None:
            return
        try:
            for rec_dict in self.storage.list_published_recommendations(limit=2000):
                rec = PublishedRecommendation.from_dict(rec_dict)
                self._recs[rec.recommendation_id] = rec
            logger.info("[recommendations] 恢复 %d 条已发布推荐", len(self._recs))
        except Exception:
            logger.exception("[recommendations] 恢复失败，跳过（不影响启动）")