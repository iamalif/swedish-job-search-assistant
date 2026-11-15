import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from jobassist import brief as B, db, demo, evaluate as EV, extract, ingest, service, workflow as W
from jobassist.cli import main
from jobassist.profile import Profile
from jobassist.web.app import create_app
from tests.test_assess import PROFILE
from tests.test_ingest import FakeClient, make_hit, SEARCH

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
FULL = dict(PROFILE, projects=[{"id": "p.x", "title": "Academic proj", "kind": "academic", "skills": ["python"],
                                 "summary": "Coursework study.", "caveat": "Academic only; not deployed."}])


def make(tmp_path, n=30, swedish=True):
    conn = db.connect(str(tmp_path / "w.sqlite"))
    from jobassist import prefs as P
    P.save(conn, {"swedish_required": "exclude"})
    hits = [make_hit(i, title=f"Job {i}", employer=f"Emp{i}", muni=f"City{i}",
                     text="Krav: Python, SQL, GCP." if i % 2 else "Spark and Airflow required." + (" Fluent Swedish required." if swedish else "")) for i in range(1, n + 1)]
    ingest.refresh(conn, FakeClient(hits), SEARCH, NOW)
    for h in hits:
        extract.store_requirements(conn, "jobtech", h["id"])
    return conn


def rows(conn):
    return service.assess_all(conn, Profile(FULL), NOW)


def test_feedback_validation_and_latest_wins(tmp_path):
    conn = make(tmp_path, 3)
    W.add_feedback(conn, "1", "unsuitable", NOW, "language")
    W.add_feedback(conn, "1", "worth_investigating", NOW)
    assert W.latest_feedback(conn)["1"]["label"] == "worth_investigating"
    with pytest.raises(ValueError):
        W.add_feedback(conn, "1", "maybe", NOW)
    with pytest.raises(ValueError):
        W.add_feedback(conn, "1", "unsuitable", NOW, reason="vibes")
    with pytest.raises(KeyError):
        W.add_feedback(conn, "nope", "unsuitable", NOW)
    assert "2" not in W.latest_feedback(conn)           # unlabelled = unknown, not negative


def test_label_queue_mixes_top_and_explore_skips_labelled_and_dupes(tmp_path):
    conn = db.connect(str(tmp_path / "q.sqlite"))
    hits = [make_hit(i, title=f"Job {i}", employer=f"E{i}", muni=f"C{i}", text="Krav: Python, SQL, GCP." if i < 8 else "Spark required. Airflow required.") for i in range(1, 16)]
    hits += [make_hit(100, title="Job 1", employer="E1", muni="C1", text="Krav: Python, SQL, GCP.")]  # repost of #1
    ingest.refresh(conn, FakeClient(hits), SEARCH, NOW)
    for h in hits:
        extract.store_requirements(conn, "jobtech", h["id"])
    W.add_feedback(conn, "2", "unsuitable", NOW)
    q = W.label_queue(rows(conn), W.latest_feedback(conn), n=8)
    ids = [x["job"]["source_id"] for x in q]
    assert "2" not in ids and len(ids) == len(set(ids))
    assert not ({"1", "100"} <= set(ids))                # cross-posts never both queued
    assert {x["sample"] for x in q} == {"top", "explore"}


def test_eval_metrics_on_toy_labels(tmp_path):
    conn = make(tmp_path, 20, swedish=False)
    rs = rows(conn)
    for r in rs:
        sid = r["job"]["source_id"]
        W.add_feedback(conn, sid, "worth_investigating" if int(sid) % 2 else "unsuitable", NOW)
    act = [r for r in rows(conn) if r["status"] == "active"]
    res = EV.evaluate(act, W.latest_feedback(conn), {"baseline": EV.baseline_scores(act), "tfidf": EV.tfidf_scores(act, Profile(FULL))})
    assert res["n_labelled"] <= 20 and res["n_pos"] + res["n_neg"] == res["n_labelled"] == res["n_labelled_in_comparison"]
    assert res["rankers"]["baseline"]["pairwise_auc"] >= 0.9    # odd jobs are the matching ones
    assert res["false_hard_exclusions"] == []
    # a perfect inversion scores ~0
    inv = {k: -v for k, v in EV.baseline_scores(act).items()}
    assert EV.evaluate(act, W.latest_feedback(conn), {"inv": inv})["rankers"]["inv"]["pairwise_auc"] <= 0.1


def test_wilson_and_not_sure_ignored():
    lo, hi = EV.wilson(8, 10)
    assert 0.4 < lo < 0.5 and 0.9 < hi < 1.0 and EV.wilson(0, 0) == (0.0, 1.0)


def test_false_hard_exclusion_is_reported(tmp_path):
    conn = make(tmp_path, 4)
    W.add_feedback(conn, "2", "worth_investigating", NOW)    # job 2 is Excluded (Swedish required)
    act = [r for r in rows(conn) if r["status"] == "active"]
    res = EV.evaluate(act, W.latest_feedback(conn), {"b": EV.baseline_scores(act)})
    assert [x["id"] for x in res["false_hard_exclusions"]] == ["2"]


def test_saved_job_lifecycle(tmp_path):
    conn = make(tmp_path, 2)
    W.save_job(conn, "1", NOW, "considering", "looks good")
    W.save_job(conn, "1", datetime(2026, 11, 20, tzinfo=timezone.utc), "applied", applied_on="2026-11-20")
    r = conn.execute("SELECT * FROM saved_jobs").fetchone()
    assert r["status"] == "applied" and r["notes"] == "looks good" and r["applied_on"] == "2026-11-20"
    assert r["first_saved"].startswith("2026-11-15") and r["status_changed"].startswith("2026-11-20")
    with pytest.raises(ValueError):
        W.save_job(conn, "1", NOW, "hired")
    W.unsave_job(conn, "1")
    assert conn.execute("SELECT count(*) FROM saved_jobs").fetchone()[0] == 0


def test_brief_uses_only_profile_facts(tmp_path):
    conn = make(tmp_path, 1)
    x = rows(conn)[0]
    b = B.build_brief(x["a"], x["job"], Profile(FULL))
    ev_ids = {e["id"] for e in FULL["evidence"]}
    assert b["emphasise"] and all(e["evidence_id"] in ev_ids for e in b["emphasise"])
    gcp = next(e for e in b["emphasise"] if e["evidence_id"] == "skill.gcp")
    assert gcp["basis"] == "managed" and "managed" in gcp["how_to_say_it"].lower() and gcp["caveat"] == "managed only"
    assert b["projects"] and b["projects"][0]["caveat"] == "Academic only; not deployed."
    text = json.dumps(b).lower()
    assert "deployed to production" not in text and "%" not in "".join(i["prepare"] for i in b["interview_examples"])


def test_brief_gap_means_unknown(tmp_path):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    ingest.refresh(conn, FakeClient([make_hit(1, text="Krav: Python, Kubernetes.")]), SEARCH, NOW)
    extract.store_requirements(conn, "jobtech", "1")
    x = rows(conn)[0]
    b = B.build_brief(x["a"], x["job"], Profile(FULL))
    assert any("unknown, not absent" in g["text"] for g in b["gaps"])


def test_web_flows(tmp_path):
    conn = make(tmp_path, 6)
    pf = tmp_path / "p.json"; pf.write_text(json.dumps(FULL))
    c = TestClient(create_app(str(tmp_path / "w.sqlite"), str(pf)))
    lab = c.get("/label").text
    import re
    visible = re.sub(r"<style.*?</style>|<input[^>]*>", "", lab, flags=re.S)
    assert "Label jobs" in lab
    assert not any(w in visible for w in ("Prioritise", "Worth reviewing", "Stretch", "Significant barrier"))  # blind
    assert c.post("/feedback", data={"source_id": "1", "label": "worth_investigating", "reason": "role_content", "sample": "top", "category": "Prioritise", "next": "/label"}, follow_redirects=False).status_code == 303
    assert W.latest_feedback(conn)["1"]["sample"] == "top"
    assert c.post("/feedback", data={"source_id": "1", "label": "bogus"}).status_code == 400
    assert c.post("/job/1/save", data={"status": "preparing", "notes": "cv tweak"}, follow_redirects=False).status_code == 303
    assert "cv tweak" in c.get("/saved").text and "Job 1" in c.get("/saved").text
    assert "Preparation brief" in c.get("/job/1/brief").text and c.get("/job/999/brief").status_code == 404
    assert "Ranker evaluation" in c.get("/evaluation").text
    assert c.post("/job/1/save", data={"remove": "1"}, follow_redirects=False).status_code == 303
    assert "Nothing saved yet" in c.get("/saved").text


def test_demo_build_is_synthetic_and_works(tmp_path):
    out = str(tmp_path / "demo.sqlite")
    assert demo.build(out, NOW) == 8
    conn = db.connect(out)
    rs = service.assess_all(conn, Profile.load(demo.PROFILE), NOW)
    cats = {r["job"]["title"]: r["a"].category for r in rs}
    assert cats["Data Scientist (DEMO)"] == "Prioritise" and cats["Analytics Specialist (DEMO)"] == "Significant barrier"
    assert cats["Research Engineer (DEMO)"] == "Significant barrier"
    assert all(r["job"]["url"].startswith("https://example.invalid") for r in rs)
    c = TestClient(create_app(out, str(demo.PROFILE)))
    assert "<script>alert(1)" not in c.get("/job/9000008").text


def test_export_and_backup(tmp_path, capsys):
    conn = make(tmp_path, 2)
    W.add_feedback(conn, "1", "unsuitable", NOW); conn.close()
    db_path = str(tmp_path / "w.sqlite")
    assert main(["--db", db_path, "export", str(tmp_path / "e.json")]) == 0
    data = json.loads((tmp_path / "e.json").read_text())
    assert len(data["feedback"]) == 1 and "jobs" not in data
    assert main(["--db", db_path, "backup", str(tmp_path / "b.sqlite")]) == 0
    assert db.connect(str(tmp_path / "b.sqlite")).execute("SELECT count(*) FROM jobs").fetchone()[0] == 2


def test_excluded_labelled_jobs_leave_the_ranker_comparison_but_are_reported(tmp_path):
    conn = make(tmp_path, 6)                     # even jobs are Excluded (Swedish required)
    for i in range(1, 7):
        W.add_feedback(conn, str(i), "unsuitable" if i % 2 else "worth_investigating", NOW)
    act = [r for r in rows(conn) if r["status"] == "active"]
    res = EV.evaluate(act, W.latest_feedback(conn), {"b": EV.baseline_scores(act)})
    assert res["n_labelled"] == 6 and res["n_labelled_in_comparison"] == 3
    assert {x["id"] for x in res["false_hard_exclusions"]} == {"2", "4", "6"}


@pytest.mark.skipif(not __import__("jobassist.embed", fromlist=["x"]).available(), reason="optional embeddings extra not installed")
def test_embeddings_cache_and_cross_language_similarity(tmp_path):
    from jobassist import embed
    v = embed.embed_texts(["Vi söker en dataanalytiker med erfarenhet av SQL", "We need a data analyst with SQL experience",
                           "Vi söker en barnskötare till förskolan"])
    assert float(v[0] @ v[1]) > 0.6 > float(v[0] @ v[2])
    conn = make(tmp_path, 4, swedish=False)
    act = [r for r in rows(conn) if r["status"] == "active"]
    st = embed.ensure_job_embeddings(conn, act)
    assert st["computed"] == 4 and embed.ensure_job_embeddings(conn, act)["cached"] == 4 and embed.cache_complete(conn, act)
    sc = embed.embedding_scores(conn, act, Profile(FULL))
    assert set(sc) == {r["job"]["source_id"] for r in act}
    assert embed.chunk("T", "word " * 1000).__len__() == embed.MAX_CHUNKS


def test_refresh_button_runs_in_background_and_reports(tmp_path):
    import time
    from tests.test_ingest import FakeClient
    path = str(tmp_path / "r.sqlite")
    db.connect(path).close()
    cfg = tmp_path / "s.toml"; cfg.write_text('[[search]]\ntrack = "data"\nquery = "x"\n')
    c = TestClient(create_app(path, None, client_factory=lambda: FakeClient([make_hit(1), make_hit(2)]), searches_path=str(cfg)))
    assert c.post("/refresh", follow_redirects=False).status_code == 303
    for _ in range(50):
        if "Refresh ok" in c.get("/").text:
            break
        time.sleep(0.1)
    html = c.get("/").text
    assert "Refresh ok: 2 fetched, 2 new" in html and "Refresh jobs now" in html
    assert db.connect(path).execute("SELECT count(*) FROM requirements").fetchone()[0] >= 0


def test_semantic_sort_option_is_safe_without_cache(tmp_path):
    conn = make(tmp_path, 3)
    pf = tmp_path / "p.json"; pf.write_text(json.dumps(FULL))
    c = TestClient(create_app(str(tmp_path / "w.sqlite"), str(pf)))
    assert c.get("/?sort=semantic").status_code == 200       # no embeddings cached: falls back to rules order


def test_label_queue_fills_shortfall_and_small_batches(tmp_path):
    conn = make(tmp_path, 6, swedish=False)            # only 'top'-type jobs (odd) plus Stretch; tiny pool
    rs = rows(conn)
    for n in (2, 3, 5):
        q = W.label_queue(rs, {}, n=n)
        assert len(q) == min(n, len({r["job"]["cross_post_key"] for r in rs if r["status"] == "active"}))


def test_eval_cli_json_with_labels(tmp_path, capsys, monkeypatch):
    conn = make(tmp_path, 12, swedish=False)
    for i in range(1, 13):
        W.add_feedback(conn, str(i), "worth_investigating" if i % 2 else "unsuitable", NOW, category="x")
    conn.close()
    pf = tmp_path / "p.json"; pf.write_text(json.dumps(FULL))
    monkeypatch.setattr("jobassist.profile.DEFAULT", pf)
    assert main(["--db", str(tmp_path / "w.sqlite"), "eval"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_labelled"] > 0 and isinstance(out["category_vs_label"], dict)
    assert all(isinstance(v, dict) for v in out["category_vs_label"].values())


def test_deadline_with_explicit_offset_is_respected():
    from jobassist.ingest import job_status
    row = {"source_removed": 0, "deadline": "2026-11-15T12:00:00+00:00"}   # 13:00 Stockholm
    assert job_status(row, datetime(2026, 11, 15, 11, 30, tzinfo=timezone.utc)) == "active"
    assert job_status(row, datetime(2026, 11, 15, 12, 30, tzinfo=timezone.utc)) == "past_deadline"
    naive = {"source_removed": 0, "deadline": "2026-11-15T12:00:00"}         # 12:00 Stockholm = 11:00 UTC
    assert job_status(naive, datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc)) == "active"
    assert job_status(naive, datetime(2026, 11, 15, 11, 30, tzinfo=timezone.utc)) == "past_deadline"
