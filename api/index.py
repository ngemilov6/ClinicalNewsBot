"""Vercel Python serverless entry point.

Vercel's @vercel/python runtime installs dependencies from pyproject.toml but
does NOT install the project itself, so the ``clinical_news`` package (under
``src/``) is not importable by default. We add ``src/`` to sys.path before the
import. The runtime auto-detects the exported ASGI ``app``. Static files are
served via the static build configured in ``vercel.json``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from clinical_news.web.app import app  # noqa: E402, F401
