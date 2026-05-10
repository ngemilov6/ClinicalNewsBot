"""FastAPI factory."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from clinical_news.obs import logging as obs_logging

WEB_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_ROOT / "templates"
STATIC_DIR = WEB_ROOT / "static"


def create_app() -> FastAPI:
    obs_logging.setup()
    app = FastAPI(title="Clinical News Bot", docs_url=None, redoc_url=None)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from clinical_news.web.routes import admin, downloads, public  # noqa: WPS433

    # downloads first: their paths end in .md/.pdf which would otherwise be
    # captured by the bare /briefs/{int} route as a 422.
    app.include_router(downloads.router)
    app.include_router(admin.router)
    app.include_router(public.router)
    return app


app = create_app()
