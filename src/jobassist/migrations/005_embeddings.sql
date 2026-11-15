CREATE TABLE embeddings (
    source_id    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL,
    n_chunks     INTEGER NOT NULL,
    vectors      BLOB NOT NULL,      -- float32, n_chunks x dim, row-major
    dim          INTEGER NOT NULL,
    PRIMARY KEY (source_id, content_hash, model)
);
