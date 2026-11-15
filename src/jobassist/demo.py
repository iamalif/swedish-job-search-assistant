"""Sanitised demo: synthetic ads and a synthetic profile. No real ads or personal data."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import db, extract, ingest

ROOT = Path(__file__).resolve().parents[2]
ADS = ROOT / "fixtures" / "demo_ads.json"
PROFILE = ROOT / "fixtures" / "demo_profile.json"


class _Fake:
    def __init__(self, hits):
        self.hits = hits

    def search(self, params):
        o, l = params["offset"], params["limit"]
        return {"total": {"value": len(self.hits)}, "hits": self.hits[o:o + l]}

    def get_ad(self, _):
        return None


def build(db_path: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    conn = db.connect(db_path)
    hits = json.loads(ADS.read_text(encoding="utf-8"))
    ingest.refresh(conn, _Fake(hits), [{"track": "data", "query": "demo"}], now, verify_cap=0)
    for h in hits:
        extract.store_requirements(conn, ingest.SOURCE, str(h["id"]))
    return len(hits)
