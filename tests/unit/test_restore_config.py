"""Unit tests for RestoreConfig."""

import os
import pytest
from ado_backup.config import RestoreConfig, ALL_CATEGORIES


def test_basic_construction():
    cfg = RestoreConfig(org="myorg", archive="/tmp/backup.zip", pat="tok")
    assert cfg.org == "myorg"
    assert cfg.archive == "/tmp/backup.zip"
    assert cfg.pat == "tok"


def test_org_url_normalised():
    cfg = RestoreConfig.from_env_and_args(org="https://dev.azure.com/myorg", archive="/tmp/b.zip")
    assert cfg.org == "myorg"


def test_pat_from_env(monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "envtoken")
    cfg = RestoreConfig.from_env_and_args(org="myorg", archive="/tmp/b.zip")
    assert cfg.pat == "envtoken"


def test_safe_dict_redacts_pat():
    cfg = RestoreConfig(org="o", archive="a.zip", pat="verysecret")
    d = cfg.to_safe_dict()
    assert d["pat"] == "REDACTED"
    assert "verysecret" not in str(d)


def test_target_project_name_identity():
    cfg = RestoreConfig(org="o", archive="a.zip")
    assert cfg.target_project_name("Foo") == "Foo"


def test_target_project_name_mapped():
    cfg = RestoreConfig(org="o", archive="a.zip", project_map={"OldName": "NewName"})
    assert cfg.target_project_name("OldName") == "NewName"
    assert cfg.target_project_name("Unmapped") == "Unmapped"


def test_all_categories_constant():
    assert "repositories" in ALL_CATEGORIES
    assert "work-items" in ALL_CATEGORIES
    assert "pipelines" in ALL_CATEGORIES
    assert "wikis" in ALL_CATEGORIES


def test_defaults():
    cfg = RestoreConfig(org="o", archive="a.zip")
    assert cfg.concurrency == 4
    assert cfg.create_projects is True
    assert cfg.force is False
    assert cfg.dry_run is False
    assert cfg.categories == []
    assert cfg.projects == []
