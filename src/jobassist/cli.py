"""Phase 1 command line: refresh, list, show, status."""
from __future__ import annotations

import argparse
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import db, ingest
from .sources.jobtech import JobTechClient

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path(os.environ.get("JOBASSIST_DB", ROOT / "data" / "jobs.sqlite"))


def _fmt_deadline(row) -> str:
    if not row["deadline"]:
        return "no deadline supplied"
    d = row["deadline"][:10]
    return f"{d} (date only)" if row["deadline_date_only"] else row["deadline"].replace("T", " ")


def cmd_refresh(a, conn) -> int:
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    client = JobTechClient(api_key=os.environ.get("JOBTECH_API_KEY"))
    res = ingest.refresh(conn, client, cfg["search"], datetime.now(timezone.utc),
                         max_pages=a.max_pages, verify_cap=a.verify_cap)
    print(f"run {res.run_id}: {res.status}; fetched {res.fetched}, new {res.new}, "
          f"changed {res.changed}, removed found {res.removed_found}")
    for e in res.errors:
        print("  error:", e, file=sys.stderr)
    return 0 if res.status == "ok" else 1


def cmd_extract(a, conn) -> int:
    from .extract import store_requirements
    ids = [r["source_id"] for r in conn.execute("SELECT source_id FROM jobs")]
    n = sum(store_requirements(conn, ingest.SOURCE, i) for i in ids)
    print(f"extracted {n} requirement claims for {len(ids)} jobs (unchanged jobs skipped)")
    return 0


def cmd_prefs(a, conn) -> int:
    import json as _j
    from . import prefs as P
    if a.key:
        val = _j.loads(a.value)
        print(_j.dumps(P.save(conn, {a.key: val})[a.key]))
    else:
        for k, v in P.load(conn).items():
            print(f"{k}: {_j.dumps(v, ensure_ascii=False)}")
    return 0


def cmd_serve(a, conn) -> int:
    import threading, webbrowser
    import uvicorn
    from .web.app import create_app
    url = f"http://127.0.0.1:{a.port}"
    print(f"Job assistant running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(a.db), host="127.0.0.1", port=a.port, log_level="warning")
    return 0


def cmd_embed(a, conn) -> int:
    from . import embed, service
    from .profile import Profile
    if not embed.available():
        print('Embeddings are optional: install with  .venv/bin/pip install -e ".[embeddings]"', file=sys.stderr)
        return 1
    rows = [r for r in service.assess_all(conn, Profile.load(), datetime.now(timezone.utc)) if r["status"] == "active"]
    st = embed.ensure_job_embeddings(conn, rows, progress=lambda i, n: print(f"  {i}/{n}", file=sys.stderr))
    print(f"model {embed.MODEL}: computed {st['computed']} jobs ({st['chunks']} chunks) in {st['seconds']} s; {st['cached']} cached")
    return 0


def cmd_eval(a, conn) -> int:
    import json as _j
    from . import evaluate as EV, service, workflow as W
    from .profile import Profile
    prof = Profile.load()
    rows = [r for r in service.assess_all(conn, prof, datetime.now(timezone.utc)) if r["status"] == "active"]
    rankers = {"baseline": EV.baseline_scores(rows), "tfidf": EV.tfidf_scores(rows, prof)}
    from . import embed
    if embed.available() and embed.cache_complete(conn, rows):
        rankers["embeddings"] = embed.embedding_scores(conn, rows, prof)
    else:
        print("note: embeddings ranker skipped (run `jobassist embed` first)", file=sys.stderr)
    res = EV.evaluate(rows, W.latest_feedback(conn), rankers, k=a.k)
    print(_j.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_export(a, conn) -> int:
    """Your own data (labels, saved jobs, corrections, preferences). Not the ad corpus, not the profile."""
    import json as _j
    tables = ["feedback", "saved_jobs", "requirement_overrides", "settings"]
    data = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
    Path(a.path).write_text(_j.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", a.path, {t: len(v) for t, v in data.items()})
    return 0


def cmd_backup(a, conn) -> int:
    import sqlite3
    dst = sqlite3.connect(a.path)
    conn.backup(dst)
    dst.close()
    print("backup written to", a.path)
    return 0


def cmd_demo(a, conn) -> int:
    from . import demo
    n = demo.build(a.out)
    print(f"demo database with {n} synthetic ads: {a.out}\nrun the web app on it with:\n"
          f"  JOBASSIST_DB={a.out} JOBASSIST_PROFILE={demo.PROFILE} .venv/bin/uvicorn --factory jobassist.web.app:create_app --port 8000")
    return 0


def cmd_status(a, conn) -> int:
    now = datetime.now(timezone.utc)
    f = ingest.freshness(conn, now)
    print(f"last run: {f['last_run_status']}; last full success: {f['last_ok_at']}")
    if f["stale"]:
        print("WARNING:", f["message"])
    counts: dict[str, int] = {}
    for r in conn.execute("SELECT * FROM jobs"):
        s = ingest.job_status(r, now)
        counts[s] = counts.get(s, 0) + 1
    print("jobs:", counts or "none")
    return 0


def cmd_list(a, conn) -> int:
    now = datetime.now(timezone.utc)
    f = ingest.freshness(conn, now)
    if f["stale"]:
        print("WARNING:", f["message"])
    n = 0
    for r in conn.execute("SELECT j.*, group_concat(DISTINCT t.track) tracks FROM jobs j "
                          "LEFT JOIN job_tracks t USING(source, source_id) GROUP BY j.source, j.source_id "
                          "ORDER BY publication_date DESC"):
        s = ingest.job_status(r, now)
        if a.all or s == "active":
            n += 1
            print(f"{r['source_id']:>9} [{s}] {r['title'][:60]} | {r['employer']} | {r['municipality']} | "
                  f"deadline {_fmt_deadline(r)} | {r['tracks']}\n          {r['url']}")
            if n >= a.limit:
                break
    return 0


def cmd_show(a, conn) -> int:
    r = conn.execute("SELECT * FROM jobs WHERE source_id=?", (a.id,)).fetchone()
    if not r:
        print("not found", file=sys.stderr)
        return 1
    print(f"{r['title']} | {r['employer']} | {r['municipality']}\nstatus: "
          f"{ingest.job_status(r, datetime.now(timezone.utc))}  version {r['content_version']}  "
          f"first seen {r['first_seen']}  last seen {r['last_seen']}\nopen original: {r['url']}\n")
    print(r["description_text"] or "(no text)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jobassist")
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh")
    r.add_argument("--config", default=str(ROOT / "config" / "searches.toml"))
    r.add_argument("--max-pages", type=int, default=3)
    r.add_argument("--verify-cap", type=int, default=50)
    r.set_defaults(fn=cmd_refresh)
    sub.add_parser("extract").set_defaults(fn=cmd_extract)
    pr = sub.add_parser("prefs", help='show, or set: prefs swedish_required \'"flag"\'')
    pr.add_argument("key", nargs="?")
    pr.add_argument("value", nargs="?")
    pr.set_defaults(fn=cmd_prefs)
    sv = sub.add_parser("serve", help="start the web app on localhost")
    sv.add_argument("--port", type=int, default=8000); sv.add_argument("--no-browser", action="store_true")
    sv.set_defaults(fn=cmd_serve)
    sub.add_parser("embed", help="compute/cached local embeddings (optional extra)").set_defaults(fn=cmd_embed)
    e = sub.add_parser("eval"); e.add_argument("--k", type=int, default=10); e.set_defaults(fn=cmd_eval)
    x = sub.add_parser("export"); x.add_argument("path"); x.set_defaults(fn=cmd_export)
    b = sub.add_parser("backup"); b.add_argument("path"); b.set_defaults(fn=cmd_backup)
    d = sub.add_parser("demo"); d.add_argument("--out", default=str(ROOT / "data" / "demo.sqlite")); d.set_defaults(fn=cmd_demo)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    l = sub.add_parser("list")
    l.add_argument("--all", action="store_true", help="include past-deadline and removed")
    l.add_argument("--limit", type=int, default=30)
    l.set_defaults(fn=cmd_list)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    a = p.parse_args(argv)
    return a.fn(a, None if a.cmd in ("demo", "serve") else db.connect(a.db))


if __name__ == "__main__":
    raise SystemExit(main())
