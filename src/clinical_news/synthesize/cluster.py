"""Embedding-based clustering of last-7-day articles."""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from clinical_news.filter.embedding import deserialize, score as score_text

log = logging.getLogger(__name__)

DEFAULT_DISTANCE_THRESHOLD = 0.30  # ~0.70 cosine similarity


def load_recent(conn: sqlite3.Connection, days: int = 7) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return list(conn.execute(
        "SELECT id, source_id, external_id, url, title, summary, body, "
        "       published_at, embedding "
        "FROM articles WHERE status = 'ingested' AND published_at >= ? "
        "ORDER BY published_at DESC",
        (cutoff,),
    ))


def _embedding_for(row: sqlite3.Row) -> np.ndarray:
    if row["embedding"]:
        return deserialize(row["embedding"])
    text = f"{row['title']}. {row['summary'] or ''}"
    _, emb = score_text(text)
    return emb


def assign_clusters(conn: sqlite3.Connection, rows: list[sqlite3.Row],
                    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD) -> dict[str, list[sqlite3.Row]]:
    """Cluster the rows; persist cluster_id back to articles. Returns {cluster_id: [rows]}."""
    if not rows:
        return {}

    embs = np.vstack([_embedding_for(r) for r in rows])
    # cosine distance
    norms = np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9
    embs_n = embs / norms

    if len(rows) == 1:
        labels = [0]
    else:
        clusterer = AgglomerativeClustering(
            n_clusters=None,
            metric="cosine",
            linkage="average",
            distance_threshold=distance_threshold,
        )
        labels = clusterer.fit_predict(embs_n)

    label_to_uuid: dict[int, str] = {}
    clusters: dict[str, list[sqlite3.Row]] = {}
    for row, label in zip(rows, labels, strict=True):
        cid = label_to_uuid.setdefault(int(label), str(uuid.uuid4()))
        clusters.setdefault(cid, []).append(row)
        conn.execute("UPDATE articles SET cluster_id = ? WHERE id = ?", (cid, row["id"]))

    log.info("clustered", extra={"articles": len(rows), "clusters": len(clusters)})
    return clusters
