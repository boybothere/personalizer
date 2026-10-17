"""SQLite persistence: reports, feedback, and a subject opt-out list.

Data minimisation: raw page text is never stored (SourceDocument.text is excluded
from serialisation); only short excerpts attached to findings are kept, and
reports expire after the configured retention period.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from .models import FeedbackLabel, Report, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, subject TEXT NOT NULL, subject_key TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_subject ON reports(subject_key);
CREATE TABLE IF NOT EXISTS feedback (
    report_id TEXT NOT NULL, finding_id TEXT NOT NULL, label TEXT NOT NULL, note TEXT,
    confidence TEXT, created_at TEXT NOT NULL, PRIMARY KEY (report_id, finding_id),
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS optouts (subject_key TEXT PRIMARY KEY, created_at TEXT NOT NULL);
"""


def subject_key(name: str, organization: str | None = None) -> str:
    # Opt-outs match on name alone so a person cannot be re-researched under a different org.
    return " ".join(name.lower().split())


class Store:
    def __init__(self, path: Path, retention_days: int = 30):
        self.retention = timedelta(days=retention_days)
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.purge_expired()

    def save(self, report: Report) -> None:
        body = report.model_dump_json()
        self.db.execute(
            "INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?)",
            (report.id, report.request.name, subject_key(report.request.name), report.created_at.isoformat(),
             (report.created_at + self.retention).isoformat(), body),
        )
        self.db.commit()

    def get(self, report_id: str) -> Report | None:
        row = self.db.execute("SELECT body FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            return None
        report = Report.model_validate_json(row[0])
        labels = dict(self.db.execute("SELECT finding_id, label FROM feedback WHERE report_id = ?", (report_id,)).fetchall())
        for f in report.findings:
            f.feedback = FeedbackLabel(labels[f.id]) if f.id in labels else None
        return report

    def list(self) -> list[dict]:
        rows = self.db.execute("SELECT id, subject, created_at, expires_at FROM reports ORDER BY created_at DESC").fetchall()
        return [dict(zip(("id", "subject", "created_at", "expires_at"), r)) for r in rows]

    def delete(self, report_id: str) -> bool:
        cur = self.db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
        self.db.commit()
        return cur.rowcount > 0

    def delete_subject(self, name: str) -> int:
        cur = self.db.execute("DELETE FROM reports WHERE subject_key = ?", (subject_key(name),))
        self.db.commit()
        return cur.rowcount

    def purge_expired(self) -> int:
        cur = self.db.execute("DELETE FROM reports WHERE expires_at < ?", (utcnow().isoformat(),))
        self.db.commit()
        return cur.rowcount

    def add_feedback(self, report_id: str, finding_id: str, label: FeedbackLabel, note: str | None) -> bool:
        report = self.get(report_id)
        finding = report.finding(finding_id) if report else None
        if not finding:
            return False
        self.db.execute(
            "INSERT OR REPLACE INTO feedback VALUES (?,?,?,?,?,?)",
            (report_id, finding_id, label.value, (note or "")[:500], finding.confidence.value, utcnow().isoformat()),
        )
        self.db.commit()
        return True

    def feedback_stats(self) -> dict:
        """Precision of each confidence level against user labels: the calibration check."""
        rows = self.db.execute("SELECT confidence, label, COUNT(*) FROM feedback GROUP BY confidence, label").fetchall()
        stats: dict[str, dict] = {}
        for conf, label, n in rows:
            stats.setdefault(conf, {}).setdefault(label, 0)
            stats[conf][label] += n
        for conf, counts in stats.items():
            judged = counts.get("accurate", 0) + counts.get("incorrect", 0)
            counts["precision"] = round(counts.get("accurate", 0) / judged, 3) if judged else None
        return stats

    def opt_out(self, name: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO optouts VALUES (?, ?)", (subject_key(name), utcnow().isoformat()))
        self.db.commit()
        self.delete_subject(name)

    def is_opted_out(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM optouts WHERE subject_key = ?", (subject_key(name),)).fetchone() is not None


def apply_feedback(report: Report) -> Report:
    """Findings marked incorrect/inappropriate stop supporting suggestions; irrelevant ones are demoted."""
    bad = {f.id for f in report.findings if f.feedback in (FeedbackLabel.INCORRECT, FeedbackLabel.INAPPROPRIATE)}
    for f in report.findings:
        if f.id in bad:
            f.suitable_for_personalization = False
            f.exclusion_reason = f"marked {f.feedback.value} by user"
        elif f.feedback == FeedbackLabel.IRRELEVANT:
            f.rank_score = round(f.rank_score * 0.5, 3)
    keep = lambda items: [s for s in items if not set(s.finding_ids) & bad]  # noqa: E731
    report.professional_context = keep(report.professional_context)
    for field in ("conversation_starters", "questions", "common_ground", "prep_notes"):
        setattr(report, field, keep(getattr(report, field)))
    return report
