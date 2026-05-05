"""Restore report — written incrementally, finalized at the end."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ado_backup import __version__


@dataclass
class RestoreItemRecord:
    category: str
    status: str          # ok | skipped | error | warning
    name: str = ""
    count: int = 0
    duration_seconds: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["name"]:
            del d["name"]
        if not d["count"]:
            del d["count"]
        if not d["reason"]:
            del d["reason"]
        return d


@dataclass
class RestoreProjectRecord:
    source_name: str
    target_name: str
    items: list[RestoreItemRecord] = field(default_factory=list)

    def add(self, item: RestoreItemRecord):
        self.items.append(item)

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "target_name": self.target_name,
            "items": [i.to_dict() for i in self.items],
        }


class RestoreReport:
    def __init__(self, org: str, config, report_path: Path):
        self._lock = threading.Lock()
        self._org = org
        self._config = config
        self._started_at = datetime.now(timezone.utc)
        self._projects: dict[str, RestoreProjectRecord] = {}
        self._errors = 0
        self._warnings = 0
        self._path = report_path

    def add_project(self, source_name: str, target_name: str) -> RestoreProjectRecord:
        with self._lock:
            rec = RestoreProjectRecord(source_name=source_name, target_name=target_name)
            self._projects[source_name] = rec
            return rec

    def record(
        self,
        source_project: str,
        category: str,
        *,
        status: str,
        name: str = "",
        count: int = 0,
        duration: float = 0.0,
        reason: str = "",
    ):
        with self._lock:
            if status == "error":
                self._errors += 1
            elif status == "warning":
                self._warnings += 1
            item = RestoreItemRecord(
                category=category,
                status=status,
                name=name,
                count=count,
                duration_seconds=round(duration, 2),
                reason=reason,
            )
            if source_project in self._projects:
                self._projects[source_project].add(item)
            self._flush()

    def _flush(self):
        try:
            self._path.write_text(json.dumps(self._build(), indent=2))
        except OSError:
            pass

    def _build(self) -> dict:
        now = datetime.now(timezone.utc)
        duration = (now - self._started_at).total_seconds()
        return {
            "schema_version": "1.0",
            "tool_version": __version__,
            "target_organization": self._org,
            "started_at": self._started_at.isoformat(),
            "finished_at": now.isoformat(),
            "duration_seconds": round(duration, 1),
            "config": self._config.to_safe_dict(),
            "projects": [p.to_dict() for p in self._projects.values()],
            "summary": {
                "total_projects": len(self._projects),
                "errors": self._errors,
                "warnings": self._warnings,
            },
        }

    def finalize(self) -> dict:
        with self._lock:
            self._flush()
            return self._build()["summary"]
