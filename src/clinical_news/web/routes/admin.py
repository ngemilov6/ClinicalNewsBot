"""Admin routes (HTTP Basic): relevance feedback + manual synthesis trigger."""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Response

from clinical_news.web.deps import get_db, get_settings, require_admin

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")


@router.post("/feedback/{article_id}")
def post_feedback(
    article_id: int,
    label: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    _user: str = Depends(require_admin),
):
    if label not in ("relevant", "not_relevant", "clear"):
        raise HTTPException(status_code=400, detail="invalid label")
    row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="article not found")
    new_label = None if label == "clear" else label
    conn.execute(
        "UPDATE articles SET user_label = ?, user_labeled_at = ? WHERE id = ?",
        (new_label, datetime.now(timezone.utc).isoformat(), article_id),
    )
    return Response(status_code=204)


@router.post("/generate", status_code=202)
def trigger_generate(
    background: BackgroundTasks,
    settings = Depends(get_settings),
    _user: str = Depends(require_admin),
):
    """Run the full ingest → synthesis pipeline.

    Two modes, picked automatically:

    - **Remote** (Vercel / serverless): if ``GH_REPO`` and ``GH_DISPATCH_TOKEN``
      are set, fire a GitHub ``repository_dispatch`` event so the workflow
      runs the heavy work on a dedicated runner. This is the only mode that
      works on Vercel since the function timeout is 60s and the pipeline
      takes much longer.
    - **Local** (your laptop / a long-running server): no GitHub config
      needed. The pipeline runs in-process via a FastAPI background task
      and the page polls ``/admin/status`` until a new brief lands.
    """
    repo = os.environ.get("GH_REPO", "")
    token = os.environ.get("GH_DISPATCH_TOKEN", "")

    if repo and token:
        return _dispatch_remote(repo, token)

    # Local mode: spawn the pipeline in a background task. The browser polls
    # /admin/status every 20s and reloads when a new synthesis_runs row appears.
    from clinical_news.pipeline import run_full_pipeline
    background.add_task(run_full_pipeline, settings, dry_run=False)
    return {"status": "scheduled", "mode": "local"}


def _dispatch_remote(repo: str, token: str) -> dict:
    event = os.environ.get("GH_DISPATCH_EVENT", "generate")
    url = f"https://api.github.com/repos/{repo}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "event_type": event,
        "client_payload": {
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=15)
    except httpx.HTTPError as exc:
        log.warning("GitHub dispatch failed", extra={"err": str(exc)})
        raise HTTPException(status_code=502, detail="GitHub API unreachable") from exc

    if resp.status_code >= 300:
        log.warning("GitHub dispatch non-2xx",
                    extra={"status": resp.status_code, "body": resp.text[:200]})
        raise HTTPException(
            status_code=502,
            detail=f"GitHub returned {resp.status_code}: {resp.text[:120]}",
        )
    return {"status": "dispatched", "mode": "remote", "event": event}


@router.get("/status")
def admin_status(
    conn: sqlite3.Connection = Depends(get_db),
    _user: str = Depends(require_admin),
):
    """Used by the polling UI to detect when a new brief has landed."""
    row = conn.execute(
        "SELECT id, ran_at, status, headline FROM synthesis_runs "
        "ORDER BY ran_at DESC LIMIT 1"
    ).fetchone()
    return {"latest_brief": dict(zip(row.keys(), list(row))) if row else None}
