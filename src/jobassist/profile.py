"""Verified profile evidence. Loaded from a private JSON file (git-ignored `profile/profile.json`).
Every match the assessor reports cites an evidence id from this file."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT = Path(os.environ.get("JOBASSIST_PROFILE", Path(__file__).resolve().parents[2] / "profile" / "profile.json"))


class Profile:
    def __init__(self, data: dict | None = None):
        d = data or {"evidence": [], "degrees": [], "languages": {}}
        self.data = d
        self.by_skill: dict[str, list[dict]] = {}
        for ev in d.get("evidence", []):
            for s in ev.get("skills", []):
                self.by_skill.setdefault(s.lower(), []).append(ev)
        self.languages = d.get("languages", {})
        self.degrees = d.get("degrees", [])

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Profile":
        p = Path(path) if path else DEFAULT
        if not p.exists():
            return cls()
        return cls(json.loads(p.read_text(encoding="utf-8")))

    def evidence_for(self, skill: str) -> list[dict]:
        return self.by_skill.get(skill.lower(), [])

    def degree(self, level: str) -> dict | None:
        return next((d for d in self.degrees if d["level"] == level), None)
