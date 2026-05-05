"""Unit tests for manifest module."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from ado_backup.manifest import Manifest, ItemRecord


def _make_config():
    cfg = MagicMock()
    cfg.to_safe_dict.return_value = {"org": "test", "pat": "REDACTED"}
    return cfg


def test_manifest_creates_file(tmp_path):
    m = Manifest("testorg", _make_config(), tmp_path)
    m.add_project("Proj1", "id-1")
    m.record_item("Proj1", "repositories", status="ok", name="repo1", bytes_written=1000)
    m.finalize()
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert data["organization"] == "testorg"
    assert data["schema_version"] == "1.0"
    assert len(data["projects"]) == 1
    assert data["projects"][0]["name"] == "Proj1"


def test_manifest_error_counting(tmp_path):
    m = Manifest("org", _make_config(), tmp_path)
    m.add_project("P", "id")
    m.record_item("P", "repos", status="error", reason="oops")
    m.record_item("P", "wikis", status="error", reason="oops2")
    m.finalize()
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert data["summary"]["errors"] == 2


def test_manifest_skipped_increments_warnings(tmp_path):
    m = Manifest("org", _make_config(), tmp_path)
    m.add_project("P", "id")
    m.record_item("P", "test-plans", status="skipped", reason="no license")
    m.finalize()
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert data["summary"]["warnings"] == 1


def test_manifest_thread_safety(tmp_path):
    import threading
    m = Manifest("org", _make_config(), tmp_path)
    m.add_project("P", "id")

    errors = []
    def worker(i):
        try:
            m.record_item("P", f"cat-{i}", status="ok", count=i)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    m.finalize()
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert len(data["projects"][0]["items"]) == 20


def test_item_record_omits_empty_fields(tmp_path):
    item = ItemRecord(category="repos", status="ok", name="", bytes=0, count=0, duration_seconds=1.5)
    d = item.to_dict()
    assert "name" not in d
    assert "bytes" not in d
    assert "count" not in d
    assert d["duration_seconds"] == 1.5
