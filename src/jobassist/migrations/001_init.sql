-- Source text and raw payloads are kept apart from derived claims (added in later phases).
CREATE TABLE refresh_runs (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL CHECK (status IN ('running','ok','partial','failed')),
    fetched       INTEGER NOT NULL DEFAULT 0,
    new_jobs      INTEGER NOT NULL DEFAULT 0,
    changed_jobs  INTEGER NOT NULL DEFAULT 0,
    removed_found INTEGER NOT NULL DEFAULT 0,
    errors        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE jobs (
    source            TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    url               TEXT NOT NULL,
    title             TEXT NOT NULL,
    employer          TEXT,
    employer_org_no   TEXT,
    municipality      TEXT,
    region            TEXT,
    description_text  TEXT,
    publication_date  TEXT,
    deadline          TEXT,            -- as supplied, naive Europe/Stockholm
    deadline_date_only INTEGER NOT NULL DEFAULT 0,  -- 1 when supplied time is the 23:59:59 padding
    source_timestamp  INTEGER,
    source_removed    INTEGER NOT NULL DEFAULT 0,
    removed_date      TEXT,
    removal_evidence  TEXT,            -- 'search-absent-stub' / 'stream' / null
    content_hash      TEXT NOT NULL,
    content_version   INTEGER NOT NULL DEFAULT 1,
    cross_post_key    TEXT,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    last_changed      TEXT NOT NULL,
    last_run_id       INTEGER REFERENCES refresh_runs(id),
    PRIMARY KEY (source, source_id)
);
CREATE INDEX jobs_cross_post ON jobs(cross_post_key);

CREATE TABLE job_raw (
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    raw_json    TEXT NOT NULL,         -- contact fields stripped before storage
    PRIMARY KEY (source, source_id),
    FOREIGN KEY (source, source_id) REFERENCES jobs(source, source_id)
);

CREATE TABLE job_revisions (
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    version       INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    seen_at       TEXT NOT NULL,
    title         TEXT,
    description_text TEXT,
    PRIMARY KEY (source, source_id, version),
    FOREIGN KEY (source, source_id) REFERENCES jobs(source, source_id)
);

CREATE TABLE job_tracks (
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    track         TEXT NOT NULL,
    query         TEXT NOT NULL,
    first_matched TEXT NOT NULL,
    last_matched  TEXT NOT NULL,
    PRIMARY KEY (source, source_id, track, query),
    FOREIGN KEY (source, source_id) REFERENCES jobs(source, source_id)
);
