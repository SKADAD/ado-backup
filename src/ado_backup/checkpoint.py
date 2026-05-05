"""Checkpoint store for resumable backups.

Uses a .checkpoints.jsonl file (one JSON object per line) to record
completed work units. The runner checks this before doing work and skips
already-completed items unless --force is set.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional


class Checkpoint:
    def __init__(self, run_dir: Path, force: bool = False):
        self._path = run_dir / ".checkpoints.jsonl"
        self._force = force
        self._lock = threading.Lock()
        self._done: set[str] = set()
        if not force:
            self._load()

    def _load(self):
        if not self._path.exists():
            return
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        self._done.add(rec["key"])
                    except (json.JSONDecodeError, KeyError):
                        pass

    def is_done(self, key: str) -> bool:
        if self._force:
            return False
        with self._lock:
            return key in self._done

    def mark_done(self, key: str, **meta):
        with self._lock:
            if key in self._done:
                return
            self._done.add(key)
            rec = {"key": key, **meta}
            with open(self._path, "a") as f:
                f.write(json.dumps(rec) + "\n")


def make_key(*parts: str) -> str:
    return "/".join(str(p) for p in parts)
