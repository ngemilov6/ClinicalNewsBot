"""Vercel Python serverless entry point.

Vercel's @vercel/python runtime auto-detects an ASGI ``app`` exported from a
module under ``api/``. We re-export the FastAPI app here so the whole web UI
runs as a single function. Static files are served through Vercel's static
build, configured in ``vercel.json``.
"""
from clinical_news.web.app import app  # noqa: F401
