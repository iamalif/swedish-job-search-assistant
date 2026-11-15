"""Feedback labels, saved-job tracking and the blind labelling queue."""
from __future__ import annotations

import random
from datetime import datetime

from .ingest import iso

LABELS = ("worth_investigating", "unsuitable", "not_sure")
REASONS = ("language", "location", "role_content", "seniority", "assessment_inaccurate", "other")
STATUSES = ("considering", "preparing", "applied", "interview", "closed")


def add_feedback(conn, source_id: str, label: str, now: datetime, reason: str | None = None, note: str = "",
                 sample: str = "manual", category: str | None = None) -> None:
    if label not in LABELS:
        raise ValueError(f"label must be one of {LABELS}")
    if reason and reason not in REASONS:
        raise ValueError(f"reason must be one of {REASONS}")
    j = conn.execute("SELECT content_version FROM jobs WHERE source_id=?", (source_id,)).fetchone()
    if j is None:
        raise KeyError(source_id)
    conn.execute("INSERT INTO feedback (source_id, label, reason, note, sample, category_at_label, content_version, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (source_id, label, reason, note, sample, category, j["content_version"], iso(now)))
    conn.commit()


def latest_feedback(conn) -> dict[str, dict]:
    """Newest label per job. Unclicked jobs have no entry: they are NOT negatives."""
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM feedback ORDER BY id"):
        out[r["source_id"]] = dict(r)
    return out


def save_job(conn, source_id: str, now: datetime, status: str = "considering", notes: str | None = None,
             applied_on: str | None = None, interview_on: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if conn.execute("SELECT 1 FROM jobs WHERE source_id=?", (source_id,)).fetchone() is None:
        raise KeyError(source_id)
    ts = iso(now)
    cur = conn.execute("SELECT * FROM saved_jobs WHERE source_id=?", (source_id,)).fetchone()
    if cur is None:
        conn.execute("INSERT INTO saved_jobs (source_id, status, notes, first_saved, status_changed, applied_on, interview_on) "
                     "VALUES (?,?,?,?,?,?,?)", (source_id, status, notes or "", ts, ts, applied_on, interview_on))
    else:
        conn.execute("UPDATE saved_jobs SET status=?, notes=?, status_changed=?, applied_on=?, interview_on=? WHERE source_id=?",
                     (status, cur["notes"] if notes is None else notes,
                      ts if status != cur["status"] else cur["status_changed"],
                      applied_on if applied_on is not None else cur["applied_on"],
                      interview_on if interview_on is not None else cur["interview_on"], source_id))
    conn.commit()


def unsave_job(conn, source_id: str) -> None:
    conn.execute("DELETE FROM saved_jobs WHERE source_id=?", (source_id,))
    conn.commit()


def label_queue(rows: list[dict], feedback: dict, n: int = 20, seed: int = 1, top_share: float = 0.5) -> list[dict]:
    """Blind queue: half from the top of the ranking, half random from everything else (exploration),
    so the baseline is not judged only on jobs it already recommends. Skips labelled jobs and
    likely cross-posts of labelled/queued jobs. Each item carries `sample` = 'top' | 'explore'."""
    seen_keys = {r["job"]["cross_post_key"] for r in rows if r["job"]["source_id"] in feedback}
    pool = [r for r in rows if r["status"] == "active" and r["job"]["source_id"] not in feedback]
    top = [r for r in pool if r["a"].category in ("Prioritise", "Worth reviewing")]
    rest = [r for r in pool if r["a"].category not in ("Prioritise", "Worth reviewing")]
    rng = random.Random(seed)
    rng.shuffle(top)
    rng.shuffle(rest)
    out: list[dict] = []
    for sample, src, quota in (("top", top, int(n * top_share)), ("explore", rest, n - int(n * top_share))):
        taken = 0
        for r in src:
            k = r["job"]["cross_post_key"]
            if k in seen_keys:
                continue
            seen_keys.add(k)
            out.append({**r, "sample": sample})
            taken += 1
            if taken >= quota:
                break
    if len(out) < n:                                   # a stratum ran dry: fill from what is left
        for sample, src in (("top", top), ("explore", rest)):
            for r in src:
                k = r["job"]["cross_post_key"]
                if len(out) >= n:
                    break
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                out.append({**r, "sample": sample})
    rng.shuffle(out)
    return out
