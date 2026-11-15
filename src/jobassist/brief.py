"""Preparation brief: reorganises VERIFIED profile facts against one ad. Nothing is invented.

Every emphasis item cites a profile evidence/project id and the ad quote it answers; wording of
claims comes from the profile file (label/where/summary/caveat), never from the ad or a model.
"""
from __future__ import annotations

BASIS_NOTE = {
    "managed": "You managed delivery; engineers implemented it. Say so plainly.",
    "coursework": "Academic work. Do not describe it as commercial or deployed.",
    "partial": "Related, not the full skill. Describe the part you actually did.",
    "hands_on": "",
}


def build_brief(assessment, job, profile) -> dict:
    a = assessment
    emphasise, interview, seen_ev = [], [], set()
    matched_skills = set()
    for m in a.matches:
        matched_skills.add(m["skill"])
        for e in m["evidence"]:
            full = next((x for x in profile.data["evidence"] if x["id"] == e["id"]), {})
            emphasise.append({"ad_asks": m["skill"], "ad_quote": m["quote"], "evidence_id": e["id"], "label": e["label"],
                              "basis": e["basis"], "where": full.get("where", ""), "caveat": e.get("caveat", ""),
                              "how_to_say_it": BASIS_NOTE.get(e["basis"], "")})
            if e["id"] not in seen_ev and full.get("where"):
                seen_ev.add(e["id"])
                interview.append({"evidence_id": e["id"], "prepare": f"A concrete example for {e['label']}: {full['where']}",
                                  "basis": e["basis"]})
    projects = []
    for p in profile.data.get("projects", []):
        hit = sorted(matched_skills & {s.lower() for s in p.get("skills", [])})
        if hit:
            projects.append({"id": p["id"], "title": p["title"], "kind": p["kind"], "summary": p["summary"],
                             "caveat": p["caveat"], "relevant_to": hit})
    gaps = [{"text": f"'{u['skill']}' is asked ({u['status']}) and you have no evidence on file. This means unknown, "
                     "not absent: add real evidence to your profile if you have it.", "quote": u["quote"]} for u in a.unmatched]
    gaps += [{"text": b["text"], "quote": b["quote"]} for b in a.barriers]
    clarify = [{"text": u["text"], "quote": u["quote"]} for u in a.unknowns]
    for e in emphasise:
        if e["basis"] == "managed":
            clarify.append({"text": f"Expect to be asked what you personally built for '{e['ad_asks']}' versus managed.",
                            "quote": e["ad_quote"]})
            break
    return {"job": {"id": job["source_id"], "title": job["title"], "employer": job["employer"], "url": job["url"]},
            "category": a.category, "emphasise": emphasise, "projects": projects, "gaps": gaps, "clarify": clarify,
            "interview_examples": interview,
            "rules": ["Only profile facts are used.", "Academic work and proposals are never presented as deployed.",
                      "No metrics are added beyond those in the profile file."]}
