"""Unit tests for restore runner (archive extraction and manifest parsing)."""

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ado_backup.config import RestoreConfig
from ado_backup.restore_report import RestoreReport
from ado_backup.restore_runner import run_restore


def _make_config(archive: str, org="targetorg", dry_run=True, projects=None):
    return RestoreConfig(
        org=org,
        archive=archive,
        pat="tok",
        dry_run=dry_run,
        projects=projects or [],
    )


def _make_config_obj():
    cfg = MagicMock()
    cfg.to_safe_dict.return_value = {}
    return cfg


def _make_report(tmp_path) -> RestoreReport:
    return RestoreReport("targetorg", _make_config_obj(), tmp_path / "report.json")


def _build_archive(base: Path) -> Path:
    """Build a minimal valid backup archive."""
    run_dir = base / "run"
    (run_dir / "projects" / "MyProject").mkdir(parents=True)
    manifest = {
        "schema_version": "1.0",
        "tool_version": "1.0.0",
        "organization": "sourceorg",
        "started_at": "2024-01-01T00:00:00+00:00",
        "finished_at": "2024-01-01T00:10:00+00:00",
        "duration_seconds": 600,
        "config": {},
        "projects": [{"name": "MyProject", "id": "proj-id-1", "items": []}],
        "summary": {"total_projects": 1, "total_repositories": 0, "total_work_items": 0, "errors": 0, "warnings": 0},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    zip_path = base / "backup.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in run_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(base))
    return zip_path


def test_missing_archive_returns_1(tmp_path):
    cfg = _make_config(str(tmp_path / "nonexistent.zip"))
    report = _make_report(tmp_path)
    result = run_restore(cfg, report)
    assert result == 1


def test_bad_zip_returns_1(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    cfg = _make_config(str(bad))
    report = _make_report(tmp_path)
    result = run_restore(cfg, report)
    assert result == 1


def test_dry_run_succeeds(tmp_path, capsys):
    zip_path = _build_archive(tmp_path)
    cfg = _make_config(str(zip_path), dry_run=True)
    report = _make_report(tmp_path)
    result = run_restore(cfg, report)
    assert result == 0
    out = capsys.readouterr().out
    assert "MyProject" in out


def test_dry_run_project_filter(tmp_path, capsys):
    zip_path = _build_archive(tmp_path)
    # Only restore a project that doesn't exist in the archive
    cfg = _make_config(str(zip_path), dry_run=True, projects=["NonExistentProject"])
    report = _make_report(tmp_path)
    result = run_restore(cfg, report)
    # Should return 1 because no matching projects
    assert result == 1


def test_uses_directory_directly(tmp_path):
    """run_restore should accept an unpacked directory, not just a ZIP."""
    run_dir = tmp_path / "run"
    (run_dir / "projects" / "P1").mkdir(parents=True)
    manifest = {
        "schema_version": "1.0",
        "tool_version": "1.0.0",
        "organization": "src",
        "started_at": "2024-01-01T00:00:00+00:00",
        "finished_at": "2024-01-01T00:10:00+00:00",
        "duration_seconds": 1,
        "config": {},
        "projects": [{"name": "P1", "id": "id1", "items": []}],
        "summary": {"total_projects": 1, "total_repositories": 0, "total_work_items": 0, "errors": 0, "warnings": 0},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    cfg = _make_config(str(run_dir), dry_run=True)
    report = _make_report(tmp_path)
    result = run_restore(cfg, report)
    assert result == 0
