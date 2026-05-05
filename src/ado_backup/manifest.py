"""Manifest tracking — written incrementally during the run, finalized at the end."""

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
class ItemRecord:
    category: str
    status: str  # ok | skipped | error
    name: str = ""
    bytes: int = 0
    count: int = 0
    duration_seconds: float = 0.0
    reason: str = ""  # for skipped/error

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v or v == 0}
        # Remove zero-value noise
        if d.get("bytes") == 0:
            del d["bytes"]
        if d.get("count") == 0:
            del d["count"]
        if d.get("name") == "":
            d.pop("name", None)
        if d.get("reason") == "":
            d.pop("reason", None)
        return d


@dataclass
class ProjectRecord:
    name: str
    id: str
    items: list[ItemRecord] = field(default_factory=list)

    def add_item(self, item: ItemRecord):
        self.items.append(item)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "id": self.id,
            "items": [i.to_dict() for i in self.items],
        }


class Manifest:
    def __init__(self, org: str, config, run_dir: Path):
        self._lock = threading.Lock()
        self._org = org
        self._config = config
        self._run_dir = run_dir
        self._started_at = datetime.now(timezone.utc)
        self._projects: dict[str, ProjectRecord] = {}
        self._errors = 0
        self._warnings = 0
        self._path = run_dir / "manifest.json"

    def add_project(self, name: str, project_id: str) -> ProjectRecord:
        with self._lock:
            rec = ProjectRecord(name=name, id=project_id)
            self._projects[name] = rec
            return rec

    def record_item(
        self,
        project_name: str,
        category: str,
        *,
        status: str,
        name: str = "",
        bytes_written: int = 0,
        count: int = 0,
        duration: float = 0.0,
        reason: str = "",
    ):
        with self._lock:
            if status == "error":
                self._errors += 1
            if status == "skipped" and reason:
                self._warnings += 1
            item = ItemRecord(
                category=category,
                status=status,
                name=name,
                bytes=bytes_written,
                count=count,
                duration_seconds=round(duration, 2),
                reason=reason,
            )
            if project_name in self._projects:
                self._projects[project_name].add_item(item)
            self._flush()

    def record_org_item(self, category: str, *, status: str, count: int = 0, reason: str = ""):
        with self._lock:
            if status == "error":
                self._errors += 1
            # Org-level items stored as a synthetic project "__org__"
            if "__org__" not in self._projects:
                self._projects["__org__"] = ProjectRecord(name="__org__", id="")
            item = ItemRecord(category=category, status=status, count=count, reason=reason)
            self._projects["__org__"].add_item(item)
            self._flush()

    def _flush(self):
        """Write manifest to disk (called under lock)."""
        try:
            self._path.write_text(json.dumps(self._build_dict(), indent=2))
        except OSError:
            pass

    def _build_dict(self) -> dict:
        now = datetime.now(timezone.utc)
        duration = (now - self._started_at).total_seconds()
        real_projects = [p for k, p in self._projects.items() if k != "__org__"]
        return {
            "schema_version": "1.0",
            "tool_version": __version__,
            "organization": self._org,
            "started_at": self._started_at.isoformat(),
            "finished_at": now.isoformat(),
            "duration_seconds": round(duration, 1),
            "config": self._config.to_safe_dict(),
            "projects": [p.to_dict() for p in real_projects],
            "summary": {
                "total_projects": len(real_projects),
                "total_repositories": self._count_category("repositories"),
                "total_work_items": self._count_category_count("work-items"),
                "errors": self._errors,
                "warnings": self._warnings,
            },
        }

    def _count_category(self, category: str) -> int:
        count = 0
        for p in self._projects.values():
            for item in p.items:
                if item.category == category and item.status == "ok":
                    count += 1
        return count

    def _count_category_count(self, category: str) -> int:
        total = 0
        for p in self._projects.values():
            for item in p.items:
                if item.category == category and item.status == "ok":
                    total += item.count
        return total

    def finalize(self):
        with self._lock:
            self._flush()

    def summary(self) -> dict:
        with self._lock:
            return self._build_dict()["summary"]
