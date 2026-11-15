"""Normalise, store and refresh job ads. Source-agnostic apart from `normalize_jobtech`.

Invariants (Phase 1 gate):
* repeated refresh never duplicates (PK = source + source_id);
* a failed refresh never deletes or removes anything and is recorded as failed/partial;
* "not returned by a bounded search" is NOT treated as removal; removal needs evidence
  from /ad/{id} (removed stub or 404);
* contact data is stripped before raw payloads are stored.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .sources.jobtech import SourceError

SOURCE = "jobtech"
STOCKHOLM = ZoneInfo("Europe/Stockholm")
STALE_AFTER_HOURS = 24


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ws(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def strip_contacts(hit: dict) -> dict:
    """Return a copy of the raw ad without recruiter/employer contact fields."""
    h = copy.deepcopy(hit)
    h["application_contacts"] = []
    if isinstance(h.get("employer"), dict):
        h["employer"]["email"] = None
        h["employer"]["phone_number"] = None
    if isinstance(h.get("application_details"), dict):
        h["application_details"]["email"] = None
    return h


def normalize_jobtech(hit: dict) -> dict:
    addr = hit.get("workplace_address") or {}
    emp = hit.get("employer") or {}
    deadline = hit.get("application_deadline")
    sid = str(hit["id"])
    title = _ws(hit.get("headline"))
    text = (hit.get("description") or {}).get("text") or ""
    org = emp.get("organization_number")
    muni = addr.get("municipality")
    h = hashlib.sha256(json.dumps(
        [title, _ws(emp.get("name")), _ws(text), deadline, muni], ensure_ascii=False).encode()
    ).hexdigest()
    cross = hashlib.sha1(
        f"{(org or _ws(emp.get('name'))).lower()}|{title.lower()}|{(muni or '').lower()}".encode()
    ).hexdigest()[:16]
    return {
        "source": SOURCE, "source_id": sid,
        "url": hit.get("webpage_url") or f"https://arbetsformedlingen.se/platsbanken/annonser/{sid}",
        "title": title, "employer": emp.get("name"), "employer_org_no": org,
        "municipality": muni, "region": addr.get("region"),
        "description_text": text, "publication_date": hit.get("publication_date"),
        "deadline": deadline,
        "deadline_date_only": 1 if deadline and deadline.endswith("T23:59:59") else 0,
        "source_timestamp": hit.get("timestamp"),
        "content_hash": h, "cross_post_key": cross,
    }


def job_status(row: sqlite3.Row | dict, now: datetime) -> str:
    """active | past_deadline | removed. Date-only deadlines expire after that Stockholm day."""
    if row["source_removed"]:
        return "removed"
    dl = row["deadline"]
    if dl:
        try:
            end = datetime.fromisoformat(dl)
            # Source deadlines are naive Stockholm local time; an explicit offset is honoured, not overwritten.
            end = end.replace(tzinfo=STOCKHOLM) if end.tzinfo is None else end
            if now.astimezone(STOCKHOLM) > end:
                return "past_deadline"
        except ValueError:
            pass  # unparseable deadline: leave active, never fabricate expiry
    return "active"


def upsert_job(conn: sqlite3.Connection, hit: dict, run_id: int, now: datetime) -> str:
    n = normalize_jobtech(hit)
    ts = iso(now)
    cur = conn.execute("SELECT content_hash, content_version FROM jobs WHERE source=? AND source_id=?",
                       (n["source"], n["source_id"])).fetchone()
    raw = json.dumps(strip_contacts(hit), ensure_ascii=False)
    cols = ("url", "title", "employer", "employer_org_no", "municipality", "region",
            "description_text", "publication_date", "deadline", "deadline_date_only",
            "source_timestamp", "content_hash", "cross_post_key")
    if cur is None:
        conn.execute(
            "INSERT INTO jobs (source, source_id, " + ",".join(cols) +
            ", source_removed, first_seen, last_seen, last_changed, last_run_id) VALUES (?,?," +
            ",".join("?" * len(cols)) + ",0,?,?,?,?)",
            (n["source"], n["source_id"], *[n[c] for c in cols], ts, ts, ts, run_id))
        result, version = "new", 1
    else:
        changed = cur["content_hash"] != n["content_hash"]
        version = cur["content_version"] + (1 if changed else 0)
        sets = ",".join(f"{c}=?" for c in cols)
        conn.execute(
            f"UPDATE jobs SET {sets}, content_version=?, last_seen=?, last_run_id=?, "
            "source_removed=0, removed_date=NULL, removal_evidence=NULL"
            + (", last_changed=?" if changed else "") + " WHERE source=? AND source_id=?",
            (*[n[c] for c in cols], version, ts, run_id, *([ts] if changed else []),
             n["source"], n["source_id"]))
        result = "changed" if changed else "unchanged"
    if result in ("new", "changed"):
        conn.execute("INSERT OR REPLACE INTO job_revisions VALUES (?,?,?,?,?,?,?)",
                     (n["source"], n["source_id"], version, n["content_hash"], ts,
                      n["title"], n["description_text"]))
    conn.execute("INSERT OR REPLACE INTO job_raw VALUES (?,?,?,?)",
                 (n["source"], n["source_id"], ts, raw))
    return result


@dataclass
class RefreshResult:
    run_id: int
    status: str
    fetched: int = 0
    new: int = 0
    changed: int = 0
    removed_found: int = 0
    errors: list[str] = field(default_factory=list)


def refresh(conn: sqlite3.Connection, client, searches: list[dict], now: datetime,
            max_pages: int = 3, verify_cap: int = 50) -> RefreshResult:
    """Bounded refresh. `searches` items: {track, query, params?}. Never raises on source failure."""
    run_id = conn.execute("INSERT INTO refresh_runs (started_at, status) VALUES (?, 'running')",
                          (iso(now),)).lastrowid
    conn.commit()
    res = RefreshResult(run_id, "running")
    search_failures = 0
    for s in searches:
        pages_ok = 0
        try:
            for page in range(max_pages):
                params = {"q": s["query"], "limit": 100, "offset": page * 100,
                          "sort": "pubdate-desc", **s.get("params", {})}
                body = client.search(params)
                hits = body["hits"]
                with conn:  # one transaction per page: earlier good pages survive a later failure
                    for hit in hits:
                        outcome = upsert_job(conn, hit, run_id, now)
                        res.fetched += 1
                        res.new += outcome == "new"
                        res.changed += outcome == "changed"
                        ts = iso(now)
                        conn.execute(
                            "INSERT INTO job_tracks VALUES (?,?,?,?,?,?) ON CONFLICT(source,source_id,track,query) "
                            "DO UPDATE SET last_matched=excluded.last_matched",
                            (SOURCE, str(hit["id"]), s["track"], s["query"], ts, ts))
                pages_ok += 1
                total = (body.get("total") or {}).get("value", 0)
                if (page + 1) * 100 >= total or not hits:
                    break
        except SourceError as e:
            if not pages_ok:
                search_failures += 1
            res.errors.append(f"search '{s['query']}': {e}")
    res.removed_found += verify_removals(conn, client, run_id, now, verify_cap, res.errors)
    if not res.errors:
        res.status = "ok"
    elif searches and search_failures == len(searches):
        res.status = "failed"  # no search succeeded: nothing learned about the source
    else:
        res.status = "partial"
    with conn:
        conn.execute(
            "UPDATE refresh_runs SET finished_at=?, status=?, fetched=?, new_jobs=?, changed_jobs=?, "
            "removed_found=?, errors=? WHERE id=?",
            (iso(now), res.status, res.fetched,
             res.new, res.changed, res.removed_found, "\n".join(res.errors), run_id))
    return res


def verify_removals(conn, client, run_id: int, now: datetime, cap: int, errors: list[str]) -> int:
    """Check live-looking jobs that this run did not return, via /ad/{id}. Failures never imply removal."""
    rows = conn.execute(
        "SELECT source_id FROM jobs WHERE source=? AND source_removed=0 AND "
        "(last_run_id IS NOT ? ) ORDER BY last_seen ASC LIMIT ?", (SOURCE, run_id, cap)).fetchall()
    found = 0
    for r in rows:
        sid = r["source_id"]
        try:
            ad = client.get_ad(sid)
        except SourceError as e:
            errors.append(f"verify {sid}: {e}")
            continue
        ts = iso(now)
        if ad is None or ad.get("removed"):
            evidence = "ad-404" if ad is None else "ad-stub"
            with conn:
                conn.execute("UPDATE jobs SET source_removed=1, removed_date=?, removal_evidence=? "
                             "WHERE source=? AND source_id=?",
                             ((ad or {}).get("removed_date"), evidence, SOURCE, sid))
            found += 1
        elif ad.get("headline"):
            with conn:
                upsert_job(conn, ad, run_id, now)
    return found


def freshness(conn: sqlite3.Connection, now: datetime) -> dict:
    """Stale-data warning state. A failed latest run or an old last success must be visible."""
    last = conn.execute("SELECT * FROM refresh_runs WHERE status!='running' ORDER BY id DESC LIMIT 1").fetchone()
    ok = conn.execute("SELECT finished_at FROM refresh_runs WHERE status='ok' ORDER BY id DESC LIMIT 1").fetchone()
    out = {"last_run_status": last["status"] if last else None,
           "last_ok_at": ok["finished_at"] if ok else None, "stale": False, "message": ""}
    if not last:
        out.update(stale=True, message="No refresh has run yet.")
    elif ok is None:
        out.update(stale=True, message="No successful refresh yet; job list may be incomplete.")
    else:
        age = now - datetime.strptime(ok["finished_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if last["status"] != "ok":
            out.update(stale=True, message=f"Latest refresh {last['status']}: data may be stale "
                                           f"(last full success {ok['finished_at']}).")
        elif age.total_seconds() > STALE_AFTER_HOURS * 3600:
            out.update(stale=True, message=f"Last successful refresh was {ok['finished_at']} (over {STALE_AFTER_HOURS} h ago).")
    return out
