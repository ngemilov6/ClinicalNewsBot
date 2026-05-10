"""Download routes: /briefs/{id}.md (PDFs are produced by browser print)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from clinical_news.web.deps import get_db

router = APIRouter()


@router.get("/briefs/{brief_id}.md")
def download_md(brief_id: int, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute(
        "SELECT id, ran_at, output_path, body_md FROM synthesis_runs WHERE id = ?",
        (brief_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="brief not found")

    fname = f"brief-{row['ran_at'][:10]}.md"
    body = row["body_md"] or ""
    if body:
        return Response(
            body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # Legacy rows: serve from disk if available.
    if not row["output_path"]:
        raise HTTPException(status_code=404, detail="brief content missing")
    p = Path(row["output_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="markdown file no longer on disk")
    return FileResponse(p, media_type="text/markdown", filename=fname)


