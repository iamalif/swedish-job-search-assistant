# Swedish Job Search Assistant

A local, single-user tool that finds current Swedish job ads, explains how each one relates to *your own
verified evidence*, and helps you prepare. It is a decision aid for reviewing jobs faster. It does not
predict whether you would be hired.

**Status: MVP, ranking quality unvalidated.** The data pipeline and the interface are tested; how well the
ranking matches a person's real judgement has not been measured yet (see [Limitations](#limitations)).

## What it does

- Fetches ads from Arbetsförmedlingen's public JobSearch API (bounded queries per search track) into a local SQLite database.
- Keeps history: repeated refreshes never duplicate ads; a failed refresh keeps old jobs and shows a stale-data warning; removed or past-deadline ads leave the shortlist but stay in history.
- Extracts requirements from ad text with transparent rules (language, degree, years of experience, skills, work model). Each item keeps its quote and character offsets, and you can correct it.
- Compares requirements with a profile you write yourself. Every match cites an evidence entry and its basis (hands-on, coursework, managed, partial). Missing evidence is shown as unknown, never as "you lack it".
- Puts each job in a category: Prioritise, Worth reviewing, Stretch, Significant barrier or Excluded. The rules are visible, and excluded jobs stay recoverable with the quoted reason.
- Lets you label jobs, save and track applications, and generate a preparation brief built only from your profile file.
- Optional local semantic ranking (multilingual embeddings) to compare against the rule-based baseline.

No account, no cloud service, no paid API and no LLM is needed. Job retrieval sends requests to the JobSearch API. Optional embeddings require a model download; profile text is processed locally.

## Try it with synthetic data (no private files needed)

Requires Python 3.11+ (developed on 3.14, macOS arm64). Editable checkout is the supported install.

```bash
git clone <this repository> && cd swedish-job-search-assistant
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q                 # offline tests
.venv/bin/jobassist demo                      # builds data/demo.sqlite from synthetic ads; prints the run command
```

Run the printed command (it points the app at the demo database and demo profile). The demo ads are invented
and use `example.invalid` links. Their deadlines are fixed in 2027, so they will eventually show as past deadline.

## Use it with real jobs

1. Create your private profile: copy `fixtures/demo_profile.json` to `profile/profile.json` and replace it with
   your own facts. `profile/` is git-ignored. Each evidence entry has an id, label, the skills it supports, a
   `basis` (`hands_on`, `coursework`, `managed`, `partial`), where you did it, and an optional caveat. Skills you
   do not list are treated as unknown. Do not claim academic or simulated work was deployed commercially.
2. Start the app and press **Refresh jobs now**, or use the command line:

```bash
.venv/bin/jobassist serve          # http://127.0.0.1:8000, bound to localhost only
.venv/bin/jobassist refresh        # fetch ads (queries in config/searches.toml)
.venv/bin/jobassist extract        # (re)compute requirement claims
.venv/bin/jobassist prefs          # show preferences (change them at /preferences)
```

Defaults are neutral: no location, work-model, employment-type or salary filters, and jobs that explicitly
require Swedish are *flagged* as a barrier, not hidden. All of this is changeable in `/preferences`.
Search queries are in `config/searches.toml`.

### Optional: local embeddings

```bash
.venv/bin/pip install -e ".[embeddings]"
.venv/bin/jobassist embed          # one-time model download, then cached
```
Uses `paraphrase-multilingual-MiniLM-L12-v2` (Apache-2.0) through ONNX Runtime. About 756 MB was observed in the
model cache and about 170 MB for the runtime on macOS arm64. Profile text stays on your machine. It is exposed as
an *unvalidated* sort option and in the evaluation page; the rule-based order stays the default.

## Data and privacy

- **Source:** the Arbetsförmedlingen data portal lists the JobSearch and JobStream APIs under CC0. That licence
  covers the data, not this code. The app does not claim complete coverage of Swedish vacancies.
- **What is stored locally:** the ad fields and text as received, plus your profile, preferences, labels, notes
  and corrections. Application contacts, employer email/phone and the application email field are cleared before
  the raw payload is stored. **Ad free text and other fields are stored as received and can contain personal
  data.** Keep your database private and never publish it.
- Ads are treated as untrusted input: text is stored verbatim, escaped in the interface and never executed or
  followed as instructions.
- The repository contains no real ads and no real profile; `data/`, `profile/` and `internal/` are git-ignored.

## Limitations

- **Ranking is unvalidated.** Evaluation compares rankers using your own labels and reports precision among
  *labelled* jobs, not full-list precision@k. There is no frozen held-out set yet; freeze one before tuning.
- The sampler for labelling is partly category-based (half top-ranked, half random), and unlabelled jobs are
  unknown, never negative.
- Requirement extraction is rule-based and only recognises about 40 skill terms. Precision and recall are
  unmeasured. Statements without an explicit "required" or "preferred" cue stay *unspecified*.
- "Prioritise" means the ad's asked skills have evidence in your profile. It is a suggestion to review, not
  proof of suitability for a role's seniority or depth.
- Evidence of every basis counts toward a match; managed work and coursework are labelled but not weighted down.
- Only the JobSearch source is implemented. Duplicate (cross-posted) ads are grouped, not merged.
- Not packaged as a standalone wheel: config and fixtures live beside the code. `constraints.txt` records the
  versions tested on macOS arm64 with Python 3.14.
- No hosting, notifications or multi-user support. Refreshes happen only when the app is running.

## Development

Tests: `.venv/bin/python -m pytest -q` (offline) and `-m live` (one real API smoke test). Layout:
`src/jobassist/` (`sources/` fetching, `ingest`, `extract`, `profile`, `assess`, `prefs`, `workflow`,
`evaluate`, `embed`, `brief`, `web/`), `tests/`, `fixtures/` (synthetic), `config/`, `docs/`.
Architecture notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Priorities: freeze an evaluation set and collect labels, measure extraction accuracy, weight evidence bases,
then add JobStream and further sources.

## Licence

No licence has been chosen yet, so all rights are reserved by default. Add one before inviting reuse.
