"""Rule-based requirement extraction (method 'rules-v2').

Conservative by design: a requirement is 'mandatory' or 'preferred' only on an explicit cue
nearest to the mention; otherwise 'unspecified'. Ad language never implies a requirement.
Every item carries its quote and character offsets into the source text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

METHOD = "rules-v6"

W = r"(?<!\w)(?:{})(?!\w)"
MAND = re.compile(W.format(r"must|required|requirement|requirements|mandatory|essential|krävs|krav|"
                           r"obligatorisk|obligatoriskt|förutsätter|nödvändig|nödvändigt|minimum|minst"), re.I)
PREF = re.compile(W.format(r"meriterande|merit|meriter|advantage|advantageous|fördel|en fördel|plus|bonus|"
                           r"nice to have|nice-to-have|good to have|bra att ha|preferred|önskvärt|önskvärda|gärna|desirable|"
                           r"beneficial|ideally|helst"), re.I)
NEG = re.compile(r"(?:not\s+(?:strictly\s+)?(?:required|necessary|a requirement)|no\s+requirement|"
                 r"krävs\s+inte|inte\s+(?:ett\s+)?krav|ej\s+krav|är\s+inget\s+krav)", re.I)
HEADING_MAND = re.compile(r"^\s*(?:must[- ]have|requirements?|krav|obligatoriska?\s+krav|"
                          r"vi\s+kräver|required)(?:\s+\w+){0,2}\s*:?\s*$", re.I)
HEADING_PREF = re.compile(r"^\s*(?:nice[- ]to[- ]have|meriterande|meriter|bonus|önskvärt|"
                          r"preferred|it\s+is\s+a\s+plus)(?:\s+\w+){0,2}\s*:?\s*$", re.I)

LANG_CTX = re.compile(W.format(r"fluent|fluency|flytande|proficien\w*|kunskaper|kunskap|skills|speak|"
                               r"tal|skrift|written|verbal|spoken|communicat\w*|språk|language|languages|"
                               r"command|native|modersmål|kommunicera|talar|behärska\w*|svenskspråkig"), re.I)
LANGS = {"sv": re.compile(W.format(r"svenska|swedish"), re.I), "en": re.compile(W.format(r"engelska|english"), re.I)}
# "Svenska kraftnät", "Swedish Match": a company/organisation name, not a language mention.
ORG_NAME = re.compile(r"(?:Svenska|Swedish)\s+(?:(?!Language|Speaking|Skills|Proficiency|Citizens)[A-ZÅÄÖ]\w|kraftnät|kyrkan|spel\b)")
# "Swedish citizenship / work permit / engineering practices": about the country, not language ability.
NOT_LANG = re.compile(r"(?:Svenska|Swedish)\s+(?:or\s+EU|citizen\w*|work\s+permit|permit|engineering|authorit\w*|law|market|customers?|regulat\w*|standards?)", re.I)
# hedged statements are never promoted to a hard requirement
HEDGE = re.compile(W.format(r"might|may|could|possibly|can be|kan vara|kan komma|willing\w*|villig\w*|lära dig|learn\w*|interest in"), re.I)
LEVELS = [("native", r"native|modersmål"), ("fluent", r"fluent|fluency|flytande"),
          ("professional", r"professional|proficien\w*|good|goda|very good|mycket goda|excellent|utmärkt"),
          ("basic", r"basic|grundläggande|conversational")]

DEGREES = [("phd", r"ph\.?d\.?|doctorate|doktorsexamen|doktorand"),
           ("master", r"master(?:'?s)?(?:\s+degree)?|msc|m\.sc|magisterexamen|masterexamen|civilingenjör|civilingenjörsexamen"),
           ("bachelor", r"bachelor(?:'?s)?|bsc|b\.sc|kandidatexamen|högskoleexamen|högskoleutbildning|university degree|eftergymnasial")]

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "ett": 1, "två": 2, "tre": 3, "fyra": 4, "fem": 5, "sex": 6, "sju": 7, "åtta": 8}
YEARS = re.compile(r"(?<!\w)(\d{1,2}|" + "|".join(NUM_WORDS) + r")\s*(?:\+|-\s*\d{1,2})?\s*(?:years?|års?)(?!\w)", re.I)

WORK_MODELS = [("remote", r"fully remote|100% remote|remote[- ]first|remote|distansarbete|på distans|arbeta hemifrån|work from home"),
               ("hybrid", r"hybrid\w*|delvis på distans"),
               ("onsite", r"on[- ]?site|på plats|kontor(?:et)?\s+i|in[- ]office")]
GEO = re.compile(r"(?i:within|inom|in|i|from|från|based in|bosatt i)\s+(?:(?i:sweden|sverige|the eu|eu|europe)|[A-ZÅÄÖ][a-zåäö]+)")

SKILLS = ["python", "r", "sql", "postgresql", "pandas", "numpy", "scikit-learn", "pytorch", "tensorflow",
          "spark", "airflow", "dbt", "power bi", "dax", "tableau", "aws", "azure", "gcp", "docker",
          "kubernetes", "git", "llm", "nlp", "machine learning", "maskininlärning", "deep learning",
          "statistics", "statistik", "etl", "data warehouse", "streamlit", "n8n", "hubspot", "scrum",
          "agile", "java", "javascript", "typescript", "c#", "snowflake", "databricks", "mlops", "tdd", "ci/cd"]
SKILL_RE = {s: re.compile(r"(?<![\w+#/.-])" + re.escape(s) + r"(?![\w+#-])", re.I) for s in SKILLS}


@dataclass
class Req:
    type: str
    value: dict
    status: str
    quote: str
    start: int
    end: int
    method: str = METHOD

    def value_json(self) -> str:
        return json.dumps(self.value, ensure_ascii=False, sort_keys=True)


def _segments(text: str):
    """Yield (start, end) of sentence-ish segments, newline/bullet/sentence aware."""
    pos = 0
    for line in text.split("\n"):
        ls = pos
        pos += len(line) + 1
        if not line.strip():
            continue
        for m in re.finditer(r"[^.!?;]+(?:[.!?;]+|$)", line):
            seg = m.group(0)
            lead = len(seg) - len(seg.lstrip(" \t•*-–·"))
            s, e = ls + m.start() + lead, ls + m.end()
            if text[s:e].strip():
                yield s, e


def _status(seg: str, mention: tuple[int, int], section: str | None) -> str:
    if NEG.search(seg):
        return "preferred" if PREF.search(seg) else "not_required"
    best, kind = None, None
    for rx, k in ((MAND, "mandatory"), (PREF, "preferred")):
        for m in rx.finditer(seg):
            d = min(abs(m.start() - mention[1]), abs(mention[0] - m.end()))
            if best is None or d < best:
                best, kind = d, k
    if kind:
        return kind
    return section or "unspecified"


def _status_multi(seg: str, mentions: list[tuple[int, int]], section: str | None) -> list[str]:
    """Each cue is attributed to a mention in its own comma-clause if any, else the nearest mention;
    a mention left without a cue falls back to the nearest cue overall."""
    if not mentions:
        return []
    if NEG.search(seg):
        return ["preferred" if PREF.search(seg) else "not_required"] * len(mentions)
    cues = [(m.start(), m.end(), k) for rx, k in ((MAND, "mandatory"), (PREF, "preferred")) for m in rx.finditer(seg)]
    dist = lambda c, m: min(abs(c[0] - m[1]), abs(m[0] - c[1]))
    clause = lambda pos: seg.count(",", 0, pos)
    own: list[list] = [[] for _ in mentions]
    for c in cues:
        same = [i for i, m in enumerate(mentions) if clause(m[0]) == clause(c[0])] or list(range(len(mentions)))
        own[min(same, key=lambda i: dist(c, mentions[i]))].append(c)
    out = []
    for i, m in enumerate(mentions):
        pool = own[i] or cues
        out.append(min(pool, key=lambda c: dist(c, m))[2] if pool else (section or "unspecified"))
    return out


def extract_requirements(text: str) -> list[Req]:
    out: list[Req] = []
    text = text or ""
    section: str | None = None
    for s, e in _segments(text):
        seg = text[s:e]
        stripped = seg.strip()
        if HEADING_MAND.match(stripped):
            section = "mandatory"; continue
        if HEADING_PREF.match(stripped):
            section = "preferred"; continue
        if len(stripped) < 40 and stripped.endswith(":"):
            section = None  # unrelated heading ends the section
            continue

        # languages: only where the sentence is about language ability
        if LANG_CTX.search(seg) or MAND.search(seg) or PREF.search(seg) or NEG.search(seg):
            masked = NOT_LANG.sub(lambda m: "_" * len(m.group(0)), ORG_NAME.sub(lambda m: "_" * len(m.group(0)), seg))
            found = [(code, m.span()) for code, rx in LANGS.items() for m in [rx.search(masked)] if m]
            statuses = _status_multi(seg, [sp for _, sp in found], section)
            level = next((n for n, p in LEVELS if re.search(W.format(p), seg, re.I)), None)
            if HEDGE.search(seg):
                statuses = ["unspecified" if x == "mandatory" else x for x in statuses]
            for (code, _), st in zip(found, statuses):
                out.append(Req("language", {"lang": code, **({"level": level} if level else {})},
                               st, stripped, s, e))
        for name, pat in DEGREES:
            m = re.search(W.format(pat), seg, re.I)
            if m:
                field_ = re.search(r"(?:in|i|inom)\s+([A-Za-zÅÄÖåäö ,/&-]{3,60})", seg[m.end():])
                v = {"level": name}
                if field_:
                    v["field_hint"] = field_.group(1).strip(" ,")
                out.append(Req("degree", v, _status(seg, m.span(), section), stripped, s, e))
                break
        for m in YEARS.finditer(seg):
            n = m.group(1).lower()
            years = NUM_WORDS.get(n) or int(n)
            out.append(Req("experience_years", {"years": years, "context": stripped},
                           _status(seg, m.span(), section), stripped, s, e))
        for kind, pat in WORK_MODELS:
            m = re.search(W.format(pat), seg, re.I)
            if m:
                v = {"model": kind}
                g = GEO.search(seg)
                if g:
                    v["geography_hint"] = g.group(0)
                out.append(Req("work_model", v, "unspecified", stripped, s, e))
                break
        for skill, rx in SKILL_RE.items():
            m = rx.search(seg)
            if m and not (skill == "r" and not re.search(r"(?<!\w)R(?!\w)(?!\s*&)", seg)):
                out.append(Req("skill", {"skill": skill}, _status(seg, m.span(), section), stripped, s, e))
    return out


def store_requirements(conn, source: str, source_id: str) -> int:
    """(Re)compute for the job's current text. Idempotent; replaces claims for stale hashes."""
    row = conn.execute("SELECT description_text, content_hash FROM jobs WHERE source=? AND source_id=?",
                       (source, source_id)).fetchone()
    if row is None:
        return 0
    have = conn.execute("SELECT 1 FROM requirements WHERE source=? AND source_id=? AND content_hash=? AND method=? LIMIT 1",
                        (source, source_id, row["content_hash"], METHOD)).fetchone()
    if have:
        return 0
    conn.execute("DELETE FROM requirements WHERE source=? AND source_id=?", (source, source_id))
    reqs = extract_requirements(row["description_text"] or "")
    for r in reqs:
        conn.execute("INSERT INTO requirements (source, source_id, content_hash, type, value, status, quote, "
                     "char_start, char_end, method) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (source, source_id, row["content_hash"], r.type, r.value_json(), r.status, r.quote,
                      r.start, r.end, r.method))
    conn.commit()
    return len(reqs)
