"""Top-level CLI entry point."""
from __future__ import annotations

import json
import logging
import warnings

# Silence the google.generativeai sunset warning before any submodule loads.
warnings.filterwarnings("ignore", category=FutureWarning)

import click

from clinical_news import db
from clinical_news.config import Settings
from clinical_news.obs import logging as obs_logging
from clinical_news.obs.healthcheck import report as healthcheck_report

log = logging.getLogger(__name__)


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    settings = Settings.load()
    obs_logging.setup(settings.log_level)
    ctx.obj = settings


@cli.command()
@click.pass_obj
def migrate(settings: Settings) -> None:
    """Apply schema migrations (idempotent)."""
    import os
    target = os.environ.get("TURSO_DATABASE_URL") or str(settings.db_path)
    db.migrate(settings.db_path)
    click.echo(f"migrated: {target}")


@cli.command()
@click.pass_obj
def healthcheck(settings: Settings) -> None:
    """Report pipeline health for the last 7 days."""
    with db.connect(settings.db_path) as conn:
        report = healthcheck_report(conn)
    click.echo(json.dumps(report, indent=2))


@cli.command()
@click.pass_obj
def ingest(settings: Settings) -> None:
    """Run the ingest pipeline (Phase 1+)."""
    from clinical_news.pipeline import run_ingest
    run_ingest(settings)


@cli.command()
@click.option("--dry-run", is_flag=True, help="Skip email/Docs delivery.")
@click.pass_obj
def synthesize(settings: Settings, dry_run: bool) -> None:
    """Run the weekly synthesis pipeline (Phase 4+)."""
    from clinical_news.pipeline import run_synthesis
    run_synthesis(settings, dry_run=dry_run)


@cli.group()
def eval() -> None:
    """Evaluation harness."""


@eval.command("relevance")
@click.option("--include-user-labels", is_flag=True,
              help="Include articles with user-supplied labels as additional ground truth.")
@click.pass_obj
def eval_relevance(settings: Settings, include_user_labels: bool) -> None:
    from clinical_news.eval_harness import run_relevance_eval
    run_relevance_eval(settings, include_user_labels=include_user_labels)


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
@click.option("--reload", is_flag=True, help="Reload on code changes (dev only).")
def web(host: str, port: int, reload: bool) -> None:
    """Run the FastAPI reader UI."""
    import uvicorn
    uvicorn.run("clinical_news.web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    cli()
