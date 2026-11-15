"""Loads jobs + claims + preferences and produces assessments. No UI, no network."""
from __future__ import annotations

import json
from datetime import datetime

from . import assess as A, ingest, prefs as P
from .profile import Profile


def load_requirements(conn, source_id: str | None = None) -> dict[str, list[dict]]:
    ov = {(r["source_id"], r["type"], r["quote"]): r["status"]
          for r in conn.execute("SELECT * FROM requirement_overrides")}
    out: dict[str, list[dict]] = {}
    q = "SELECT r.* FROM requirements r JOIN jobs j ON j.source=r.source AND j.source_id=r.source_id " \
        "AND j.content_hash=r.content_hash" + (" WHERE r.source_id=?" if source_id else "")
    for r in conn.execute(q, (source_id,) if source_id else ()):
        status = ov.get((r["source_id"], r["type"], r["quote"]), r["status"])
        out.setdefault(r["source_id"], []).append({
            "id": r["id"], "type": r["type"], "value": json.loads(r["value"]), "status": status,
            "extracted_status": r["status"], "overridden": status != r["status"], "quote": r["quote"]})
    return out


def _extras(conn) -> dict[str, dict]:
    q = ("SELECT source_id, json_extract(raw_json,'$.employment_type.label') et, "
         "json_extract(raw_json,'$.working_hours_type.label') wh, json_extract(raw_json,'$.workplace_model.label') wm, "
         "json_extract(raw_json,'$.salary_description') sd FROM job_raw")
    return {r["source_id"]: {"employment_type": r["et"], "working_hours": r["wh"], "workplace_model": r["wm"],
                             "salary_description": r["sd"]} for r in conn.execute(q)}


def assess_all(conn, profile: Profile, now: datetime, prefs: dict | None = None):
    prefs = prefs or P.load(conn)
    reqs = load_requirements(conn)
    extras = _extras(conn)
    tracks: dict[str, list[str]] = {}
    for r in conn.execute("SELECT source_id, track FROM job_tracks"):
        tracks.setdefault(r["source_id"], []).append(r["track"])
    rows = []
    for j in conn.execute("SELECT * FROM jobs"):
        status = ingest.job_status(j, now)
        a = A.assess(j, reqs.get(j["source_id"], []), extras.get(j["source_id"], {}),
                     tracks.get(j["source_id"], []), prefs, profile)
        rows.append({"job": j, "status": status, "a": a})
    rows.sort(key=lambda x: x["a"].sort_key(prefs["track_order"]) + (-(len(x["job"]["publication_date"] or "")),))
    return rows
