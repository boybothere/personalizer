"""FastAPI app. Run: uvicorn personalizer.api:app  (binds to 127.0.0.1 by default)."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .config import Settings
from .models import FeedbackLabel, Report, ResearchRequest
from .pipeline import Pipeline
from .report import to_markdown
from .safety import PolicyViolation
from .store import Store, apply_feedback

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class FeedbackIn(BaseModel):
    label: FeedbackLabel
    note: str | None = Field(default=None, max_length=500)


class OptOutIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    pipeline = pipeline or Pipeline.from_settings(settings)
    store = store or Store(settings.db_path, settings.retention_days)
    app = FastAPI(title="Personalizer", version="0.1.0",
                  description="Evidence-backed meeting personalisation from public professional sources.")

    def auth(authorization: str | None = Header(default=None)) -> None:
        if settings.api_token and not secrets.compare_digest(authorization or "", f"Bearer {settings.api_token}"):
            raise HTTPException(401, "missing or invalid bearer token")

    def load(report_id: str) -> Report:
        r = store.get(report_id)
        if not r:
            raise HTTPException(404, "report not found (it may have expired)")
        return apply_feedback(r)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "config": settings.describe()}

    @app.post("/api/research", response_model=Report, dependencies=[Depends(auth)])
    def research(req: ResearchRequest):
        if store.is_opted_out(req.name):
            raise HTTPException(403, "this person has asked not to be researched")
        try:
            report = pipeline.run(req)
        except PolicyViolation as exc:
            raise HTTPException(422, str(exc)) from exc
        store.save(report)
        return report

    @app.get("/api/reports", dependencies=[Depends(auth)])
    def list_reports():
        return store.list()

    @app.get("/api/reports/{report_id}", response_model=Report, dependencies=[Depends(auth)])
    def get_report(report_id: str):
        return load(report_id)

    @app.get("/api/reports/{report_id}/markdown", response_class=PlainTextResponse, dependencies=[Depends(auth)])
    def get_markdown(report_id: str):
        return to_markdown(load(report_id))

    @app.delete("/api/reports/{report_id}", dependencies=[Depends(auth)])
    def delete_report(report_id: str):
        if not store.delete(report_id):
            raise HTTPException(404, "report not found")
        return {"deleted": report_id}

    @app.post("/api/reports/{report_id}/findings/{finding_id}/feedback", dependencies=[Depends(auth)])
    def feedback(report_id: str, finding_id: str, body: FeedbackIn):
        if not store.add_feedback(report_id, finding_id, body.label, body.note):
            raise HTTPException(404, "report or finding not found")
        return {"report_id": report_id, "finding_id": finding_id, "label": body.label}

    @app.get("/api/feedback/stats", dependencies=[Depends(auth)])
    def feedback_stats():
        return store.feedback_stats()

    @app.post("/api/optout", dependencies=[Depends(auth)])
    def opt_out(body: OptOutIn):
        """Record a data subject's objection: deletes their reports and blocks future research."""
        store.opt_out(body.name)
        return {"opted_out": body.name}

    @app.post("/api/admin/purge", dependencies=[Depends(auth)])
    def purge():
        return {"purged": store.purge_expired()}

    return app


def __getattr__(name: str):
    # Lazily build the module-level app so importing this module in tests has no side effects.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
