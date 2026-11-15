"""SQLite connection and forward-only SQL migrations."""
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    files = sorted(
        (f for f in resources.files("jobassist.migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    for f in files:
        version = int(f.name.split("_")[0])
        if version > current:
            conn.executescript(f.read_text(encoding="utf-8"))
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
