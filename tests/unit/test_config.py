"""Unit tests for config module."""

import os
import pytest
from ado_backup.config import BackupConfig


def test_pat_from_kwarg():
    cfg = BackupConfig.from_env_and_args(org="myorg", pat="mytoken")
    assert cfg.pat == "mytoken"
    assert cfg.org == "myorg"


def test_pat_from_env(monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "envtoken")
    cfg = BackupConfig.from_env_and_args(org="myorg")
    assert cfg.pat == "envtoken"


def test_kwarg_overrides_env(monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "envtoken")
    cfg = BackupConfig.from_env_and_args(org="myorg", pat="argtoken")
    assert cfg.pat == "argtoken"


def test_org_url_normalized():
    cfg = BackupConfig.from_env_and_args(org="https://dev.azure.com/myorg")
    assert cfg.org == "myorg"


def test_org_url_with_trailing_slash():
    cfg = BackupConfig.from_env_and_args(org="https://dev.azure.com/myorg/")
    assert cfg.org == "myorg"


def test_safe_dict_redacts_pat():
    cfg = BackupConfig.from_env_and_args(org="myorg", pat="supersecret")
    d = cfg.to_safe_dict()
    assert d["pat"] == "REDACTED"
    assert "supersecret" not in str(d)


def test_safe_dict_redacts_client_secret():
    cfg = BackupConfig.from_env_and_args(
        org="myorg",
        client_secret="verysecret",
    )
    d = cfg.to_safe_dict()
    assert d["client_secret"] == "REDACTED"


def test_safe_dict_no_pat_is_none():
    cfg = BackupConfig.from_env_and_args(org="myorg")
    d = cfg.to_safe_dict()
    assert d["pat"] is None


def test_defaults():
    cfg = BackupConfig.from_env_and_args(org="myorg")
    assert cfg.concurrency == 4
    assert cfg.runs_per_pipeline == 200
    assert cfg.runs_per_test_plan == 100
    assert cfg.output_dir == "./backups"
    assert cfg.log_format == "text"
    assert not cfg.include_logs
    assert not cfg.include_package_binaries
    assert not cfg.force
    assert not cfg.dry_run
