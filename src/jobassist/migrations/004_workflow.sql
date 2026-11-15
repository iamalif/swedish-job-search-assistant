CREATE TABLE feedback (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL DEFAULT 'jobtech',
    source_id   TEXT NOT NULL,
    label       TEXT NOT NULL CHECK (label IN ('worth_investigating','unsuitable','not_sure')),
    reason      TEXT,          -- language | location | role_content | seniority | assessment_inaccurate | other
    note        TEXT,
    sample      TEXT NOT NULL DEFAULT 'manual',   -- top | explore | manual (how it reached the user)
    category_at_label TEXT,    -- assessment shown/hidden at the time, kept for evaluation
    content_version INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX feedback_job ON feedback(source_id);

CREATE TABLE saved_jobs (
    source         TEXT NOT NULL DEFAULT 'jobtech',
    source_id      TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('considering','preparing','applied','interview','closed')),
    notes          TEXT NOT NULL DEFAULT '',
    first_saved    TEXT NOT NULL,
    status_changed TEXT NOT NULL,
    applied_on     TEXT,
    interview_on   TEXT,
    PRIMARY KEY (source, source_id)
);
