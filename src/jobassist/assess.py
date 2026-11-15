"""Transparent, rule-based assessment ('assess-v1'). A decision aid, not a probability.

Categories: Prioritise | Worth reviewing | Stretch | Significant barrier | Excluded.
Every statement carries a source quote (from the ad) and, for matches, a profile evidence id.
Unknown stays unknown: missing evidence is "no evidence on file", never "you lack this".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

VERSION = "assess-v1"
CATEGORY_RANK = {"Prioritise": 0, "Worth reviewing": 1, "Stretch": 2, "Significant barrier": 3, "Excluded": 4}
INTERNSHIP = re.compile(r"praktik|internship|\bintern\b|exjobb|examensarbete|thesis|trainee", re.I)
MONTHLY_SEK = re.compile(r"(\d{2}[  .]?\d{3})\s*(?:kr|sek|kronor)?\s*(?:/|per|i)?\s*(?:mån|month)", re.I)


@dataclass
class Assessment:
    source_id: str
    category: str
    best_track: str | None
    tracks: list[str]
    excluded_reason: dict | None = None            # {"text","quote"}
    barriers: list[dict] = field(default_factory=list)
    unknowns: list[dict] = field(default_factory=list)
    matches: list[dict] = field(default_factory=list)       # {skill,status,quote,evidence:[{id,label,basis,caveat}]}
    unmatched: list[dict] = field(default_factory=list)     # skills asked, no evidence on file
    components: dict = field(default_factory=dict)
    version: str = VERSION

    def sort_key(self, track_order: list[str]):
        rank = track_order.index(self.best_track) if self.best_track in track_order else len(track_order)
        return (CATEGORY_RANK[self.category], rank, -self.components.get("ratio", 0),
                -self.components.get("matched", 0))


def _salary_monthly(text: str | None) -> int | None:
    if not text:
        return None
    m = MONTHLY_SEK.search(text)
    return int(re.sub(r"\D", "", m.group(1))) if m else None


def _ev(e):
    return {"id": e["id"], "label": e["label"], "basis": e["basis"], "caveat": e.get("caveat", "")}


def assess(job, reqs: list[dict], extra: dict, tracks: list[str], prefs: dict, profile) -> Assessment:
    """job: jobs row; reqs: [{id,type,value(dict),status,quote}] with overrides applied;
    extra: {employment_type, working_hours, workplace_model, salary_description}."""
    track_order = prefs["track_order"]
    best = min(tracks, key=lambda t: track_order.index(t) if t in track_order else 99) if tracks else None
    a = Assessment(job["source_id"], "Stretch", best, sorted(tracks))

    def exclude(text, quote=""):
        a.category = "Excluded"
        a.excluded_reason = {"text": text, "quote": quote}
        return a

    internship = bool(INTERNSHIP.search(job["title"] or "")) or "praktik" in (extra.get("employment_type") or "").lower()

    # ---- hard filters (all driven by preferences; empty = no restriction) ----
    sw = prefs["swedish_required"]
    for r in reqs:
        if r["type"] == "language" and r["value"].get("lang") == "sv" and r["status"] == "mandatory":
            if sw == "exclude":
                return exclude("Swedish is explicitly required (your setting: exclude).", r["quote"])
            if sw == "flag":
                a.barriers.append({"text": "Swedish explicitly required; your level is "
                                           f"{profile.languages.get('sv', {}).get('level', 'unknown')}.", "quote": r["quote"]})
    if prefs["work_models"]:
        known = (extra.get("workplace_model") or "").lower()
        model = {"arbete på plats": "onsite", "hybridarbete": "hybrid", "distansarbete": "remote"}.get(known)
        if model and model not in prefs["work_models"]:
            return exclude(f"Work model '{model}' is not in your accepted list.")
    if prefs["employment_types"]:
        et = (extra.get("employment_type") or "")
        if et and not any(p.lower() in et.lower() for p in prefs["employment_types"]):
            return exclude(f"Employment type '{et}' is not in your accepted list.")
    if prefs["regions"] and job["region"] and not any(p.lower() in job["region"].lower() for p in prefs["regions"]):
        return exclude(f"Region '{job['region']}' is not in your accepted list.")
    floor = prefs["salary_floor_sek_month"]
    stated = _salary_monthly(extra.get("salary_description"))
    if floor and stated and stated < floor and not (internship and prefs["salary_floor_exempt_internships"]):
        return exclude(f"Stated salary {stated} SEK/month is below your floor of {floor}.", extra["salary_description"])

    # ---- barriers and unknowns ----
    for r in reqs:
        t, v, st = r["type"], r["value"], r["status"]
        if t == "language" and v.get("lang") == "sv" and st == "unspecified" and v.get("level") in ("fluent", "native", "professional"):
            a.barriers.append({"text": f"Swedish at '{v['level']}' level is mentioned without saying required or preferred: "
                                       "probable requirement, please verify.", "quote": r["quote"]})
        elif t == "degree" and st == "mandatory":
            lvl = v.get("level")
            if lvl == "phd":
                a.barriers.append({"text": "A PhD is required; no matching degree on file.", "quote": r["quote"]})
            elif lvl == "master":
                d = profile.degree("master")
                a.unknowns.append({"text": "Master's required: yours is ongoing"
                                           + (f" (expected {d['expected']})" if d else "")
                                           + "; whether it counts at the start date is unclear.", "quote": r["quote"]})
        elif t == "experience_years" and st in ("mandatory", "unspecified") and v["years"] >= 3:
            a.unknowns.append({"text": f"{v['years']}+ years of experience asked. Total tenure is not substituted for the "
                                       "specific topic: compare it yourself.", "quote": r["quote"]})
        elif t == "work_model" and v.get("geography_hint"):
            a.unknowns.append({"text": f"Work model '{v['model']}' with location wording '{v['geography_hint']}': "
                                       "remote eligibility from your location is not established.", "quote": r["quote"]})
    big_years = any(r["type"] == "experience_years" and r["status"] == "mandatory" and r["value"]["years"] >= 5 for r in reqs)

    # ---- skill matches ----
    seen: set[str] = set()
    asked = matched = pref_matched = 0
    order = {"mandatory": 0, "unspecified": 1, "preferred": 2}
    for r in sorted((r for r in reqs if r["type"] == "skill" and r["status"] not in ("not_required", "ignore")),
                    key=lambda r: order.get(r["status"], 1)):
        sk = r["value"]["skill"]
        if sk in seen:
            continue
        seen.add(sk)
        ev = profile.evidence_for(sk)
        is_pref = r["status"] == "preferred"
        if not is_pref:
            asked += 1
        if ev:
            matched += not is_pref
            pref_matched += is_pref
            a.matches.append({"skill": sk, "status": r["status"], "quote": r["quote"], "evidence": [_ev(e) for e in ev]})
        elif not is_pref:
            a.unmatched.append({"skill": sk, "status": r["status"], "quote": r["quote"]})
    ratio = matched / asked if asked else 0.0
    a.components = {"asked": asked, "matched": matched, "ratio": round(ratio, 2), "preferred_matched": pref_matched,
                    "rule": "ratio>=0.6 & matched>=3: Prioritise; ratio>=0.35 & matched>=2: Worth reviewing; else Stretch; "
                            "barrier overrides; 5+ mandatory years caps at Worth reviewing"}

    if a.barriers:
        a.category = "Significant barrier"
    elif ratio >= 0.6 and matched >= 3:
        a.category = "Prioritise"
    elif ratio >= 0.35 and matched >= 2:
        a.category = "Worth reviewing"
    else:
        a.category = "Stretch"
    if big_years and a.category == "Prioritise":
        a.category = "Worth reviewing"
    if not asked:
        a.unknowns.append({"text": "No recognised skill terms were found in the ad text; the category reflects missing "
                                   "information, not a mismatch.", "quote": ""})
    return a
