"""Evaluation harness — runs the live filter chain against eval/relevance.jsonl
and reports precision/recall/F1 for the relevance classifier.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import click

from clinical_news.config import PROJECT_ROOT, Settings
from clinical_news.filter import embedding as emb_filter
from clinical_news.filter import keyword as kw_filter
from clinical_news.filter import llm_relevance
from clinical_news.pipeline import EMBEDDING_LOWER, EMBEDDING_UPPER, LLM_ACCEPT

log = logging.getLogger(__name__)

EVAL_PATH = PROJECT_ROOT / "eval" / "relevance.jsonl"


def _classify_text(title: str, summary: str) -> bool:
    """Mirror pipeline._classify but return only the boolean accept verdict."""
    if not kw_filter.is_candidate(title, summary):
        return False
    text = f"{title}. {summary}"
    sim, _ = emb_filter.score(text)
    if sim >= EMBEDDING_UPPER:
        return True
    if sim < EMBEDDING_LOWER:
        return False
    verdict = llm_relevance.classify(title, summary)
    return verdict["relevant"] and verdict["confidence"] >= LLM_ACCEPT


def _user_labeled_items(settings: Settings) -> list[dict]:
    """Pull user-labeled articles from the DB as additional eval items."""
    from clinical_news import db
    out: list[dict] = []
    with db.connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT id, title, summary, user_label "
            "FROM articles WHERE user_label IS NOT NULL"
        ).fetchall()
        for r in rows:
            out.append({
                "id": f"db_{r['id']}",
                "title": r["title"] or "",
                "summary": r["summary"] or "",
                "label": r["user_label"] == "relevant",
                "reason": "user-labeled",
            })
    return out


def run_relevance_eval(settings: Settings, include_user_labels: bool = False) -> None:
    if not EVAL_PATH.exists():
        click.echo(f"missing eval set: {EVAL_PATH}")
        return

    tp = fp = tn = fn = 0
    misses: list[dict] = []

    items: list[dict] = []
    for line in EVAL_PATH.read_text().splitlines():
        if not line.strip():
            continue
        items.append(json.loads(line))

    if include_user_labels:
        user_items = _user_labeled_items(settings)
        click.echo(f"adding {len(user_items)} user-labeled items to eval set", err=True)
        items.extend(user_items)

    total = len(items)
    click.echo(f"running {total} eval items (this can take several minutes due to throttle)...",
               err=True)

    for i, item in enumerate(items, start=1):
        try:
            predicted = _classify_text(item["title"], item.get("summary", ""))
        except Exception as exc:
            click.echo(f"  [{i}/{total}] error on {item['id']}: {exc}", err=True)
            continue
        actual = bool(item["label"])
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
            misses.append({"id": item["id"], "kind": "FP", "title": item["title"]})
        elif not predicted and actual:
            fn += 1
            misses.append({"id": item["id"], "kind": "FN", "title": item["title"]})
        else:
            tn += 1
        if i % 10 == 0 or i == total:
            click.echo(f"  [{i}/{total}] tp={tp} fp={fp} tn={tn} fn={fn}", err=True)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    n = tp + fp + tn + fn

    click.echo(json.dumps({
        "n": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }, indent=2))
    if misses:
        click.echo("\nmisclassifications:")
        for m in misses:
            click.echo(f"  {m['kind']} {m['id']}: {m['title'][:80]}")
