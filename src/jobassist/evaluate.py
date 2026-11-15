"""Ranker comparison on user labels. Rankers are plain functions rows -> {source_id: score}.

Labels: worth_investigating = positive, unsuitable = negative, not_sure = ignored. Jobs without a
label are unknown, never negative. One label per cross-post group so reposts do not inflate results.
"""
from __future__ import annotations

import math
import random
import re
from collections import Counter

from .profile import Profile

WORD = re.compile(r"[a-zåäöéü][a-zåäöéü+#.]{1,}", re.I)


def tokens(text: str) -> list[str]:
    return [w.lower().strip(".") for w in WORD.findall(text or "")]


def baseline_scores(rows: list[dict]) -> dict[str, float]:
    """Rows arrive sorted by the rule-based assessment; higher score = earlier."""
    return {r["job"]["source_id"]: float(len(rows) - i) for i, r in enumerate(rows)}


def profile_text(profile: Profile) -> str:
    d = profile.data
    parts = []
    for e in d.get("evidence", []):
        parts += [e["label"], e.get("where", ""), " ".join(e.get("skills", []))]
    for p in d.get("projects", []):
        parts += [p["title"], p.get("summary", ""), " ".join(p.get("skills", []))]
    return " ".join(parts)


def tfidf_scores(rows: list[dict], profile: Profile) -> dict[str, float]:
    docs = {r["job"]["source_id"]: Counter(tokens((r["job"]["title"] or "") + " " + (r["job"]["description_text"] or "")))
            for r in rows}
    n = max(len(docs), 1)
    df: Counter = Counter()
    for c in docs.values():
        df.update(c.keys())
    idf = {w: math.log((1 + n) / (1 + k)) + 1 for w, k in df.items()}

    def vec(c: Counter) -> dict[str, float]:
        v = {w: (1 + math.log(f)) * idf.get(w, math.log(1 + n) + 1) for w, f in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    q = vec(Counter(tokens(profile_text(profile))))
    return {sid: sum(q.get(w, 0.0) * x for w, x in vec(c).items()) for sid, c in docs.items()}


def wilson(pos: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = pos / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def _auc(pairs_pos: list[float], pairs_neg: list[float]) -> float | None:
    if not pairs_pos or not pairs_neg:
        return None
    s = sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pairs_pos for q in pairs_neg)
    return s / (len(pairs_pos) * len(pairs_neg))


def labelled_set(rows: list[dict], feedback: dict[str, dict]) -> dict[str, int]:
    """source_id -> 1 (positive) / 0 (negative); one per cross-post group, newest label wins."""
    by_group: dict[str, tuple[int, str, int]] = {}
    for r in rows:
        sid = r["job"]["source_id"]
        f = feedback.get(sid)
        if not f or f["label"] == "not_sure":
            continue
        g = r["job"]["cross_post_key"] or sid
        if g not in by_group or f["id"] > by_group[g][2]:
            by_group[g] = (1 if f["label"] == "worth_investigating" else 0, sid, f["id"])
    return {sid: y for y, sid, _ in by_group.values()}


def evaluate(rows: list[dict], feedback: dict[str, dict], rankers: dict[str, dict[str, float]],
             k: int = 10, boots: int = 500, seed: int = 7) -> dict:
    y_all = labelled_set(rows, feedback)
    meta = {r["job"]["source_id"]: r for r in rows}
    # Rankers are compared on jobs that survive the user's hard filters; wrongly filtered jobs are
    # reported separately as false hard exclusions so they are not hidden by this restriction.
    y = {i: v for i, v in y_all.items() if meta[i]["a"].category != "Excluded"}
    out = {"n_labelled": len(y_all), "n_labelled_in_comparison": len(y), "n_pos": sum(y_all.values()), "n_neg": len(y_all) - sum(y_all.values()),
           "n_not_sure": sum(1 for f in feedback.values() if f["label"] == "not_sure"),
           "unlabelled_active": sum(1 for r in rows if r["status"] == "active") - len(y), "rankers": {}}
    rng = random.Random(seed)
    for name, sc in rankers.items():
        ids = [i for i in y if i in sc]
        pos = [sc[i] for i in ids if y[i] == 1]
        neg = [sc[i] for i in ids if y[i] == 0]
        top = sorted(ids, key=lambda i: -sc[i])[:k]
        tp = sum(y[i] for i in top)
        lo, hi = wilson(tp, len(top))
        auc = _auc(pos, neg)
        ci = None
        if auc is not None and len(pos) + len(neg) >= 10:
            bs = []
            for _ in range(boots):
                bp = [rng.choice(pos) for _ in pos]
                bn = [rng.choice(neg) for _ in neg]
                bs.append(_auc(bp, bn))
            bs.sort()
            ci = (round(bs[int(0.025 * boots)], 3), round(bs[int(0.975 * boots) - 1], 3))
        order = sorted(sc, key=lambda i: -sc[i])
        rank = {i: n + 1 for n, i in enumerate(order)}
        missed = sorted((i for i in ids if y[i] == 1 and rank[i] > len(order) // 2), key=lambda i: rank[i])
        by_track: dict[str, list[int]] = {}
        for i in top:
            by_track.setdefault(meta[i]["a"].best_track or "none", []).append(y[i])
        out["rankers"][name] = {
            f"precision@{k}_among_labelled": round(tp / len(top), 3) if top else None,
            "precision_wilson95": (round(lo, 3), round(hi, 3)), "n_top": len(top),
            "pairwise_auc": None if auc is None else round(auc, 3), "auc_bootstrap95": ci,
            "positives_in_bottom_half": [{"id": i, "rank": rank[i], "title": meta[i]["job"]["title"]} for i in missed],
            "by_track_top": {t: f"{sum(v)}/{len(v)}" for t, v in by_track.items()},
        }
    out["false_hard_exclusions"] = [{"id": i, "title": meta[i]["job"]["title"],
                                      "reason": meta[i]["a"].excluded_reason["text"]}
                                     for i in y_all if y_all[i] == 1 and meta[i]["a"].category == "Excluded"]
    cvl: dict[str, dict[str, int]] = {}
    for i in y_all:                                   # JSON-safe: nested dict, no tuple keys
        cell = cvl.setdefault(meta[i]["a"].category, {"pos": 0, "neg": 0})
        cell["pos" if y_all[i] else "neg"] += 1
    out["category_vs_label"] = cvl
    return out
