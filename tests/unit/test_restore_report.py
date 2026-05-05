"""Unit tests for RestoreReport."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from ado_backup.restore_report import RestoreReport


def _make_config():
    cfg = MagicMock()
    cfg.to_safe_dict.return_value = {"org": "test"}
    return cfg


def test_report_creates_file(tmp_path):
    r = RestoreReport("myorg", _make_config(), tmp_path / "report.json")
    r.add_project("Src", "Tgt")
    r.record("Src", "repositories", status="ok", count=3, duration=1.2)
    r.finalize()
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["target_organization"] == "myorg"
    assert data["projects"][0]["source_name"] == "Src"
    assert data["projects"][0]["target_name"] == "Tgt"
    assert data["summary"]["total_projects"] == 1


def test_report_error_counting(tmp_path):
    r = RestoreReport("org", _make_config(), tmp_path / "r.json")
    r.add_project("P", "P")
    r.record("P", "repos", status="error", reason="oops")
    r.record("P", "wikis", status="error", reason="oops2")
    summary = r.finalize()
    assert summary["errors"] == 2


def test_report_warning_counting(tmp_path):
    r = RestoreReport("org", _make_config(), tmp_path / "r.json")
    r.add_project("P", "P")
    r.record("P", "secrets", status="warning", reason="secrets not restored")
    summary = r.finalize()
    assert summary["warnings"] == 1


def test_report_thread_safety(tmp_path):
    import threading
    r = RestoreReport("org", _make_config(), tmp_path / "r.json")
    r.add_project("P", "P")
    errors = []

    def worker(i):
        try:
            r.record("P", f"cat-{i}", status="ok")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    summary = r.finalize()
    assert summary["errors"] == 0
