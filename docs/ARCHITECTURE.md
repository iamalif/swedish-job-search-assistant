# Architecture notes

Single-process local app: Python, SQLite (WAL), FastAPI with server-rendered Jinja templates (autoescaped),
no JavaScript framework. Ingestion uses only the standard library.

## Modules (each testable alone)

| Module | Role |
|---|---|
| `sources/jobtech.py` | JobSearch client: pagination (limit 100), retry/backoff on 429/5xx, verified TLS |
| `ingest.py` | normalise, upsert by (source, id), content hash and revisions, freshness, removal checks |
| `extract.py` | rule-based requirements (versioned `rules-vN`), quotes plus character offsets |
| `profile.py` | loads the private evidence file |
| `prefs.py` | editable preferences stored in the database; empty list means no restriction |
| `assess.py` | transparent categories and explanations; every match cites evidence |
| `workflow.py` | feedback labels, saved jobs, blind labelling queue |
| `evaluate.py` | ranker comparison on labels (pairwise AUC, precision among labelled, intervals) |
| `embed.py` | optional local multilingual embeddings, cached per content hash |
| `brief.py` | preparation brief that only reorganises profile facts |

## Data-source behaviour (verified by live requests, November 2026)

- JobSearch works anonymously at low volume (documentation says a key is needed; treat that as possibly enforced later, `JOBTECH_API_KEY` is sent if set).
- `limit` max 100; invalid `sort` values return 400 (`pubdate-desc` is valid).
- `GET /ad/{id}` for a removed ad returns 200 with a nulled stub (`removed=true`), not 404. A 200 therefore does not prove an ad is active.
- Deadlines are naive local time; a `23:59:59` time is treated as a date-only deadline.
- `workplace_model`, `must_have.languages` and salary are often empty, so remote status, language demands and pay are usually unknown unless the text says so.

## Invariants

- Source text and raw payloads are stored apart from derived claims; claims carry the content hash they were derived from, so a changed ad invalidates them.
- A failed refresh never deletes or marks anything removed. Absence from a bounded search is not removal; removal needs `/ad/{id}` evidence.
- Hard exclusions come only from explicit user preferences and are always recoverable with the quoted reason; unknown requirements never exclude.
