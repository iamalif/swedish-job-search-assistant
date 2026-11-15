"""Local inbox UI. Server-rendered; Jinja autoescape is on, ad text is never rendered as HTML."""
from __future__ import annotations

import os
import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import brief as B, db, evaluate as EV, ingest, prefs as P, service, workflow as W
from ..profile import Profile

HERE = Path(__file__).parent
PAGE = 50


def create_app(db_path: str | None = None, profile_path: str | None = None, client_factory=None,
               searches_path: str | None = None) -> FastAPI:
    path = db_path or os.environ.get("JOBASSIST_DB") or str(HERE.parents[2] / "data" / "jobs.sqlite")
    app = FastAPI(title="Job inbox", docs_url=None, redoc_url=None)
    profile = Profile.load(profile_path)
    refresh_state = {"running": False, "message": ""}
    lock = threading.Lock()

    def _do_refresh():
        try:
            from ..sources.jobtech import JobTechClient
            c = db.connect(path)
            client = client_factory() if client_factory else JobTechClient(api_key=os.environ.get("JOBTECH_API_KEY"))
            cfg = tomllib.loads(Path(searches_path or HERE.parents[2] / "config" / "searches.toml").read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc)
            res = ingest.refresh(c, client, cfg["search"], now)
            from ..extract import store_requirements
            for r in c.execute("SELECT source_id FROM jobs").fetchall():
                store_requirements(c, ingest.SOURCE, r["source_id"])
            note = ""
            from .. import embed
            if embed.available():
                rows = [x for x in service.assess_all(c, profile, now) if x["status"] == "active"]
                embed.ensure_job_embeddings(c, rows)
            refresh_state["message"] = (f"Refresh {res.status}: {res.fetched} fetched, {res.new} new, "
                                        f"{res.changed} changed, {res.removed_found} removed." + note)
        except Exception as e:  # never leave the UI stuck on 'running'
            refresh_state["message"] = f"Refresh crashed: {type(e).__name__}: {e}"
        finally:
            refresh_state["running"] = False

    templates = Jinja2Templates(directory=str(HERE / "templates"))  # autoescape on for .html

    def conn():
        return db.connect(path)

    def deadline_label(r) -> str:
        if not r["deadline"]:
            return "No deadline supplied"
        return r["deadline"][:10] + (" (date only)" if r["deadline_date_only"] else r["deadline"][10:16].replace("T", " "))

    def explain(x) -> str:
        a = x["a"]
        if a.category == "Excluded":
            return a.excluded_reason["text"]
        bits = []
        if a.components.get("asked"):
            bits.append(f"{a.components['matched']} of {a.components['asked']} asked skills have evidence on file")
        bits += [b["text"] for b in a.barriers[:1]]
        bits += [u["text"] for u in a.unknowns[:1]]
        return "; ".join(bits) or "No recognised requirements in the text."

    @app.get("/", response_class=HTMLResponse)
    def inbox(request: Request, q: str = "", track: str = "", status: str = "active",
              category: str = "", sort: str = "rules", page: int = 1):
        now = datetime.now(timezone.utc)
        c = conn()
        pr = P.load(c)
        fresh = ingest.freshness(c, now)
        rows = []
        all_rows = service.assess_all(c, profile, now, pr)
        from .. import embed
        sem_ok = embed.available() and embed.cache_complete(c, [x for x in all_rows if x["status"] == "active"])
        sem = {}
        if sort == "semantic" and sem_ok:
            sem = embed.embedding_scores(c, [x for x in all_rows if x["status"] == "active"], profile)
            all_rows = sorted(all_rows, key=lambda x: -sem.get(x["job"]["source_id"], -1))
        for x in all_rows:
            r, a = x["job"], x["a"]
            if status != "all" and x["status"] != status:
                continue
            if q and not any(q.lower() in (r[k] or "").lower() for k in ("title", "employer", "municipality")):
                continue
            if track and track not in a.tracks:
                continue
            if category and a.category != category:
                continue
            if not category and a.category == "Excluded":
                continue  # hidden by default, always recoverable via the Excluded filter
            rows.append({"r": r, "status": x["status"], "deadline": deadline_label(r), "a": a, "why": explain(x)})
        excluded_n = sum(1 for x in all_rows if x["a"].category == "Excluded" and x["status"] == "active") if not category else 0
        total = len(rows)
        page = max(page, 1)
        rows = rows[(page - 1) * PAGE: page * PAGE]
        tracks = [t[0] for t in c.execute("SELECT DISTINCT track FROM job_tracks ORDER BY 1")]
        return templates.TemplateResponse(request, "inbox.html", {
            "rows": rows, "total": total, "fresh": fresh, "q": q, "track": track, "status": status,
            "category": category, "tracks": tracks, "page": page, "pages": max(1, -(-total // PAGE)),
            "excluded_n": excluded_n, "sort": sort, "sem_ok": sem_ok, "refresh": refresh_state, "categories": ["Prioritise", "Worth reviewing", "Stretch",
                                                     "Significant barrier", "Excluded"]})

    @app.post("/refresh")
    def start_refresh():
        with lock:
            if not refresh_state["running"]:
                refresh_state.update(running=True, message="")
                threading.Thread(target=_do_refresh, daemon=True).start()
        return RedirectResponse("/", status_code=303)

    @app.get("/job/{source_id}", response_class=HTMLResponse)
    def job(request: Request, source_id: str):
        c = conn()
        r = c.execute("SELECT * FROM jobs WHERE source_id=?", (source_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown job")
        now = datetime.now(timezone.utc)
        x = next(y for y in service.assess_all(c, profile, now) if y["job"]["source_id"] == source_id)
        revs = c.execute("SELECT version, seen_at FROM job_revisions WHERE source_id=? ORDER BY version",
                         (source_id,)).fetchall()
        dupes = c.execute("SELECT source_id, title, employer, municipality FROM jobs WHERE cross_post_key=? "
                          "AND source_id!=?", (r["cross_post_key"], source_id)).fetchall()
        reqs = service.load_requirements(c, source_id).get(source_id, [])
        saved = c.execute("SELECT * FROM saved_jobs WHERE source_id=?", (source_id,)).fetchone()
        fb = W.latest_feedback(c).get(source_id)
        return templates.TemplateResponse(request, "job.html", {
            "r": r, "status": x["status"], "deadline": deadline_label(r), "a": x["a"], "reqs": reqs,
            "revs": revs, "dupes": dupes, "fresh": ingest.freshness(c, now), "saved": saved, "fb": fb,
            "labels": W.LABELS, "reasons": W.REASONS, "save_statuses": W.STATUSES,
            "statuses": ["mandatory", "preferred", "unspecified", "not_required", "ignore"]})

    @app.post("/job/{source_id}/override")
    def override(source_id: str, type: str = Form(...), quote: str = Form(...), status: str = Form(...), note: str = Form("")):
        c = conn()
        if status not in ("mandatory", "preferred", "unspecified", "not_required", "ignore", "reset"):
            raise HTTPException(400, "bad status")
        if status == "reset":
            c.execute("DELETE FROM requirement_overrides WHERE source_id=? AND type=? AND quote=?", (source_id, type, quote))
        else:
            c.execute("INSERT INTO requirement_overrides VALUES ('jobtech',?,?,?,?,?,?) ON CONFLICT(source,source_id,type,quote) "
                      "DO UPDATE SET status=excluded.status, note=excluded.note",
                      (source_id, type, quote, status, note, ingest.iso(datetime.now(timezone.utc))))
        c.commit()
        return RedirectResponse(f"/job/{source_id}", status_code=303)

    @app.get("/preferences", response_class=HTMLResponse)
    def preferences(request: Request, saved: int = 0):
        c = conn()
        return templates.TemplateResponse(request, "prefs.html", {
            "p": P.load(c), "saved": saved, "choices": P.CHOICES, "fresh": ingest.freshness(c, datetime.now(timezone.utc))})

    @app.post("/preferences")
    def save_preferences(swedish_required: str = Form(...), work_models: list[str] = Form([]),
                         employment_types: str = Form(""), regions: str = Form(""),
                         salary_floor: str = Form(""), exempt_internships: str = Form(""), track_order: str = Form("")):
        split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
        floor = int(salary_floor) if salary_floor.strip().isdigit() else None
        P.save(conn(), {"swedish_required": swedish_required, "work_models": work_models,
                        "employment_types": split(employment_types), "regions": split(regions),
                        "salary_floor_sek_month": floor, "salary_floor_exempt_internships": bool(exempt_internships),
                        "track_order": split(track_order) or P.DEFAULTS["track_order"]})
        return RedirectResponse("/preferences?saved=1", status_code=303)

    def _feedback_back(source_id: str):
        return RedirectResponse(f"/job/{source_id}", status_code=303)

    @app.post("/feedback")
    def feedback(source_id: str = Form(...), label: str = Form(...), reason: str = Form(""), note: str = Form(""),
                 sample: str = Form("manual"), category: str = Form(""), next: str = Form("")):
        c = conn()
        try:
            W.add_feedback(c, source_id, label, datetime.now(timezone.utc), reason or None, note,
                           sample if sample in ("top", "explore", "manual") else "manual", category or None)
        except (ValueError, KeyError):
            raise HTTPException(400, "bad feedback")
        return RedirectResponse(next if next in ("/label",) else f"/job/{source_id}", status_code=303)

    @app.get("/label", response_class=HTMLResponse)
    def label(request: Request, n: int = 10):
        c = conn()
        now = datetime.now(timezone.utc)
        rows = service.assess_all(c, profile, now)
        fb = W.latest_feedback(c)
        queue = W.label_queue(rows, fb, n=min(max(n, 2), 30))
        return templates.TemplateResponse(request, "label.html", {
            "queue": queue, "done": len(EV.labelled_set(rows, fb)), "reasons": W.REASONS,
            "fresh": ingest.freshness(c, now)})

    @app.post("/job/{source_id}/save")
    def save(source_id: str, status: str = Form("considering"), notes: str = Form(None),
             applied_on: str = Form(None), interview_on: str = Form(None), remove: str = Form("")):
        c = conn()
        try:
            if remove:
                W.unsave_job(c, source_id)
            else:
                W.save_job(c, source_id, datetime.now(timezone.utc), status, notes, applied_on or None, interview_on or None)
        except (ValueError, KeyError):
            raise HTTPException(400, "bad request")
        return RedirectResponse(f"/job/{source_id}" if not remove else "/saved", status_code=303)

    @app.get("/saved", response_class=HTMLResponse)
    def saved(request: Request):
        c = conn()
        now = datetime.now(timezone.utc)
        rows = []
        for r in c.execute("SELECT s.*, j.title, j.employer, j.municipality, j.url, j.deadline, j.deadline_date_only, "
                           "j.source_removed FROM saved_jobs s JOIN jobs j ON j.source_id=s.source_id ORDER BY s.status_changed DESC"):
            rows.append({"r": r, "status_now": ingest.job_status(r, now), "deadline": deadline_label(r)})
        return templates.TemplateResponse(request, "saved.html", {
            "rows": rows, "statuses": W.STATUSES, "fresh": ingest.freshness(c, now)})

    @app.get("/job/{source_id}/brief", response_class=HTMLResponse)
    def brief(request: Request, source_id: str):
        c = conn()
        now = datetime.now(timezone.utc)
        x = next((y for y in service.assess_all(c, profile, now) if y["job"]["source_id"] == source_id), None)
        if x is None:
            raise HTTPException(404, "Unknown job")
        return templates.TemplateResponse(request, "brief.html", {
            "b": B.build_brief(x["a"], x["job"], profile), "r": x["job"], "fresh": ingest.freshness(c, now)})

    @app.get("/evaluation", response_class=HTMLResponse)
    def evaluation(request: Request):
        c = conn()
        now = datetime.now(timezone.utc)
        rows = service.assess_all(c, profile, now)
        act = [r for r in rows if r["status"] == "active"]
        fb = W.latest_feedback(c)
        rankers = {"baseline (rules)": EV.baseline_scores(act), "text similarity (TF-IDF)": EV.tfidf_scores(act, profile)}
        from .. import embed
        if embed.available() and embed.cache_complete(c, act):   # never computed inside a request
            rankers["local embeddings"] = embed.embedding_scores(c, act, profile)
        res = EV.evaluate(act, fb, rankers)
        return templates.TemplateResponse(request, "evaluation.html", {"res": res, "fresh": ingest.freshness(c, now)})

    return app


app = create_app() if os.environ.get("JOBASSIST_AUTOCREATE") else None
