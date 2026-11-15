-- Derived claims, kept apart from source text. Tied to the content hash so a changed ad
-- invalidates them; user overrides survive recomputation (matched by quote + type).
CREATE TABLE requirements (
    id             INTEGER PRIMARY KEY,
    source         TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    type           TEXT NOT NULL,   -- language | degree | experience_years | skill | work_model
    value          TEXT NOT NULL,   -- JSON
    status         TEXT NOT NULL,   -- mandatory | preferred | unspecified | not_required
    quote          TEXT NOT NULL,
    char_start     INTEGER NOT NULL,  -- offsets into jobs.description_text of that content_hash
    char_end       INTEGER NOT NULL,
    method         TEXT NOT NULL,   -- e.g. rules-v1
    FOREIGN KEY (source, source_id) REFERENCES jobs(source, source_id)
);
CREATE INDEX requirements_job ON requirements(source, source_id);

CREATE TABLE requirement_overrides (
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    type          TEXT NOT NULL,
    quote         TEXT NOT NULL,
    status        TEXT NOT NULL,    -- user-corrected status
    note          TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (source, source_id, type, quote)
);
