from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from jobassist import db, extract, ingest, prefs as P, service
from jobassist.profile import Profile
from jobassist.web.app import create_app
from tests.test_ingest import FakeClient, make_hit, SEARCH

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
PROFILE = {"languages": {"sv": {"level": "A1"}}, "degrees": [{"id": "d.msc", "level": "master", "status": "ongoing", "expected": "2028-01"}],
           "evidence": [{"id": "skill.python", "label": "Python", "skills": ["python"], "basis": "hands_on"},
                        {"id": "skill.sql", "label": "SQL", "skills": ["sql"], "basis": "hands_on"},
                        {"id": "skill.gcp", "label": "GCP", "skills": ["gcp"], "basis": "managed", "caveat": "managed only"}]}


def setup(tmp_path, text, title="Data scientist", **hit_kw):
    path = str(tmp_path / "a.sqlite")
    conn = db.connect(path)
    P.save(conn, {"swedish_required": "exclude", "salary_floor_sek_month": 30000})  # explicit test choices
    ingest.refresh(conn, FakeClient([make_hit(1, title=title, text=text, **hit_kw)]), SEARCH, NOW)
    extract.store_requirements(conn, "jobtech", "1")
    return path, conn


def cat(conn, profile=None):
    rows = service.assess_all(conn, Profile(PROFILE if profile is None else profile), NOW)
    return rows[0]["a"]


def test_swedish_required_excluded_with_quote_and_changeable(tmp_path):
    _, conn = setup(tmp_path, "Fluent Swedish is required. Python and SQL.")
    a = cat(conn)
    assert a.category == "Excluded" and "Fluent Swedish" in a.excluded_reason["quote"]
    P.save(conn, {"swedish_required": "flag"})                    # goals change: one setting
    a = cat(conn)
    assert a.category == "Significant barrier" and a.barriers
    P.save(conn, {"swedish_required": "ignore"})
    assert cat(conn).category != "Excluded"


def test_swedish_preferred_never_excluded(tmp_path):
    _, conn = setup(tmp_path, "Python and SQL are required. Swedish is a plus.")
    assert cat(conn).category not in ("Excluded", "Significant barrier")


def test_unspecified_fluent_swedish_is_flagged_not_silently_excluded(tmp_path):
    _, conn = setup(tmp_path, "Fluent Swedish, spoken and written. Python, SQL.")
    a = cat(conn)
    assert a.category == "Significant barrier" and "verify" in a.barriers[0]["text"]


def test_matches_trace_to_evidence_ids_and_unmatched_is_unknown(tmp_path):
    _, conn = setup(tmp_path, "Krav:\n- Python\n- SQL\n- GCP\n- Kubernetes")
    a = cat(conn)
    assert all(m["evidence"] and all(e["id"].startswith("skill.") for e in m["evidence"]) for m in a.matches)
    assert {m["skill"] for m in a.matches} == {"python", "sql", "gcp"}
    assert [m["skill"] for m in a.unmatched] == ["kubernetes"]
    assert a.components["asked"] == 4 and a.components["matched"] == 3
    assert [e["basis"] for m in a.matches if m["skill"] == "gcp" for e in m["evidence"]] == ["managed"]


def test_prioritise_needs_enough_evidence(tmp_path):
    _, conn = setup(tmp_path, "Krav:\n- Python\n- SQL\n- GCP")
    assert cat(conn).category == "Prioritise"
    _, conn2 = setup(tmp_path / "x" if False else tmp_path, "Python required. Spark, Airflow, dbt, Snowflake required.")
    assert cat(conn2).category == "Stretch"


def test_phd_is_barrier_ongoing_master_is_unknown_and_years_not_substituted(tmp_path):
    _, c1 = setup(tmp_path, "A PhD is required. Python.")
    assert cat(c1).category == "Significant barrier"
    _, c2 = setup(tmp_path, "A Master's degree is required. Minimum five years of experience deploying ML models. Python, SQL, GCP required.")
    a = cat(c2)
    texts = " ".join(u["text"] for u in a.unknowns)
    assert "ongoing" in texts and "Total tenure is not substituted" in texts
    assert a.category == "Worth reviewing"  # 5+ mandatory years caps Prioritise


def test_salary_floor_and_internship_exemption(tmp_path):
    low = {"salary_description": "Lön 25 000 kr per månad"}
    path = str(tmp_path / "s.sqlite")
    conn = db.connect(path)
    P.save(conn, {"salary_floor_sek_month": 30000})
    h = make_hit(1); h["salary_description"] = low["salary_description"]
    ingest.refresh(conn, FakeClient([h]), SEARCH, NOW); extract.store_requirements(conn, "jobtech", "1")
    a = cat(conn)
    assert a.category == "Excluded" and "below your floor" in a.excluded_reason["text"]
    P.save(conn, {"salary_floor_sek_month": None})
    assert cat(conn).category != "Excluded"
    P.save(conn, {"salary_floor_sek_month": 30000})
    conn.execute("UPDATE jobs SET title='Praktik data'"); conn.commit()
    assert cat(conn).category != "Excluded"        # internships exempt


def test_work_model_filter_only_when_set_and_known(tmp_path):
    path = str(tmp_path / "w.sqlite"); conn = db.connect(path)
    h = make_hit(1); h["workplace_model"] = {"label": "Arbete på plats"}
    ingest.refresh(conn, FakeClient([h]), SEARCH, NOW)
    assert cat(conn).category != "Excluded"                      # default: any
    P.save(conn, {"work_models": ["remote"]})
    assert cat(conn).category == "Excluded"
    conn.execute("UPDATE job_raw SET raw_json=json_remove(raw_json,'$.workplace_model')"); conn.commit()
    assert cat(conn).category != "Excluded"                      # unknown never excludes


def test_ui_override_rescues_false_exclusion_and_prefs_page(tmp_path):
    path, conn = setup(tmp_path, "Swedish is required. Python, SQL, GCP.")
    import json; pf = tmp_path / "p.json"; pf.write_text(json.dumps(PROFILE))
    c = TestClient(create_app(path, str(pf)))
    assert "Fluent" not in c.get("/").text and "Data scientist" not in c.get("/").text    # hidden by default
    assert "Data scientist" in c.get("/?category=Excluded").text and "Swedish is explicitly required" in c.get("/?category=Excluded").text
    reqs = service.load_requirements(conn, "1")["1"]
    lang = next(r for r in reqs if r["type"] == "language")
    r = c.post("/job/1/override", data={"type": "language", "quote": lang["quote"], "status": "preferred"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Data scientist" in c.get("/").text                  # rescued
    c.post("/preferences", data={"swedish_required": "flag", "work_models": ["remote", "hybrid"], "salary_floor": "", "track_order": "delivery, data"})
    p = P.load(conn)
    assert p["work_models"] == ["remote", "hybrid"] and p["salary_floor_sek_month"] is None and p["track_order"][0] == "delivery"
    assert "Swedish" in c.get("/preferences").text


def test_public_defaults_are_neutral(tmp_path):
    conn = db.connect(str(tmp_path / "d.sqlite"))
    p = P.load(conn)
    assert p["swedish_required"] == "flag" and p["salary_floor_sek_month"] is None
    assert p["work_models"] == [] and p["regions"] == [] and p["employment_types"] == []
    ingest.refresh(conn, FakeClient([make_hit(1, text="Fluent Swedish is required. Python. SQL.",)]), SEARCH, NOW)
    extract.store_requirements(conn, "jobtech", "1")
    a = cat(conn)
    assert a.category == "Significant barrier"           # flagged and visible, not hidden
