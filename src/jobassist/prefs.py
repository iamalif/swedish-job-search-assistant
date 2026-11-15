"""User preferences: stored in the database, editable in the UI or with `jobassist prefs`.

None of these are baked into code paths: every filter reads from here, so a change of goals
is a settings change. An empty list means "no restriction".
"""
from __future__ import annotations

import json

DEFAULTS: dict = {
    # "flag" (default): keep jobs that explicitly require Swedish and mark the barrier;
    # "exclude": hide them (recoverable with the quoted reason); "ignore": no special handling.
    "swedish_required": "flag",
    # accepted work models: any of onsite, hybrid, remote. [] = all
    "work_models": [],
    # accepted employment-type labels as given by the source (substring match). [] = all
    "employment_types": [],
    # accepted region names (substring match on source region, e.g. "Västra Götalands län"). [] = anywhere in Sweden
    "regions": [],
    # Monthly SEK floor, applied only where an ad states a clear monthly figure. None = off (default).
    # This is a personal filter, not a legal eligibility check.
    "salary_floor_sek_month": None,
    "salary_floor_exempt_internships": True,
    # search-track order, most preferred first (affects ordering, never filtering)
    "track_order": ["data", "ai-automation", "delivery"],
}

CHOICES = {
    "swedish_required": ["exclude", "flag", "ignore"],
    "work_models": ["onsite", "hybrid", "remote"],
}


def load(conn) -> dict:
    p = json.loads(json.dumps(DEFAULTS))
    for r in conn.execute("SELECT key, value FROM settings"):
        if r["key"] in p:
            p[r["key"]] = json.loads(r["value"])
    return p


def save(conn, updates: dict) -> dict:
    for k, v in updates.items():
        if k not in DEFAULTS:
            raise KeyError(f"unknown preference: {k}")
        if k == "swedish_required" and v not in CHOICES[k]:
            raise ValueError(f"{k} must be one of {CHOICES[k]}")
        if k == "work_models" and not set(v) <= set(CHOICES[k]):
            raise ValueError(f"{k} items must be in {CHOICES[k]}")
        conn.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (k, json.dumps(v, ensure_ascii=False)))
    conn.commit()
    return load(conn)
