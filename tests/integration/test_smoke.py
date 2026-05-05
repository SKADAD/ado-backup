"""Integration/smoke test — runs against a real ADO org.

Gated by ADO_TEST_ORG and ADO_TEST_PAT environment variables.
Run with: pytest -m integration
"""

import os
import json
import zipfile
from pathlib import Path

import pytest

from ado_backup.config import BackupConfig
from ado_backup.manifest import Manifest
from ado_backup.runner import run_backup
from ado_backup.zipper import zip_directory, verify_archive

ADO_TEST_ORG = os.environ.get("ADO_TEST_ORG")
ADO_TEST_PAT = os.environ.get("ADO_TEST_PAT")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def real_config(tmp_path_factory):
    if not ADO_TEST_ORG or not ADO_TEST_PAT:
        pytest.skip("ADO_TEST_ORG and ADO_TEST_PAT not set")
    tmp = tmp_path_factory.mktemp("backup")
    return BackupConfig(
        org=ADO_TEST_ORG,
        pat=ADO_TEST_PAT,
        output_dir=str(tmp),
        no_zip=True,
        force=True,
        concurrency=2,
        runs_per_pipeline=5,
        runs_per_test_plan=5,
    )


@pytest.fixture(scope="session")
def run_result(real_config, tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("run")
    (run_dir / "projects").mkdir()
    manifest = Manifest(real_config.org, real_config, run_dir)
    exit_code = run_backup(real_config, run_dir, manifest)
    return exit_code, run_dir, manifest


def test_exit_code_zero(run_result):
    exit_code, _, _ = run_result
    assert exit_code == 0


def test_manifest_exists(run_result):
    _, run_dir, _ = run_result
    assert (run_dir / "manifest.json").exists()


def test_manifest_schema(run_result):
    _, run_dir, _ = run_result
    data = json.loads((run_dir / "manifest.json").read_text())
    assert data["schema_version"] == "1.0"
    assert data["organization"] == ADO_TEST_ORG
    assert "summary" in data
    assert "projects" in data


def test_manifest_no_errors(run_result):
    _, run_dir, _ = run_result
    data = json.loads((run_dir / "manifest.json").read_text())
    assert data["summary"]["errors"] == 0


def test_organization_dir_exists(run_result):
    _, run_dir, _ = run_result
    assert (run_dir / "organization").is_dir()
    assert (run_dir / "organization" / "projects.json").exists()


def test_projects_dir_exists(run_result):
    _, run_dir, _ = run_result
    assert (run_dir / "projects").is_dir()


def test_run_log_exists(run_result):
    _, run_dir, _ = run_result
    assert (run_dir / "run.log").exists()


def test_run_log_no_secrets(run_result):
    _, run_dir, _ = run_result
    log_content = (run_dir / "run.log").read_text()
    if ADO_TEST_PAT:
        assert ADO_TEST_PAT not in log_content


def test_zip_and_verify(run_result, tmp_path):
    exit_code, run_dir, _ = run_result
    zip_path = tmp_path / "backup.zip"
    zip_directory(run_dir, zip_path)
    ok, errors = verify_archive(zip_path)
    assert ok, f"Archive verification failed: {errors}"


def test_resumable_noop(real_config, run_result, tmp_path_factory):
    """Second run into same dir should skip all checkpointed work."""
    _, run_dir, _ = run_result
    manifest2 = Manifest(real_config.org, real_config, run_dir)
    # Modify config to not force
    real_config.force = False
    exit_code2 = run_backup(real_config, run_dir, manifest2)
    assert exit_code2 == 0
