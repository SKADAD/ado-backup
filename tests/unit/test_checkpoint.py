"""Unit tests for checkpoint module."""

import json
import pytest
from pathlib import Path

from ado_backup.checkpoint import Checkpoint, make_key


def test_mark_and_check(tmp_path):
    cp = Checkpoint(tmp_path)
    key = "project/work-items/123"
    assert not cp.is_done(key)
    cp.mark_done(key, count=5)
    assert cp.is_done(key)


def test_persisted_across_instances(tmp_path):
    cp1 = Checkpoint(tmp_path)
    cp1.mark_done("a/b/c", extra="data")
    cp2 = Checkpoint(tmp_path)
    assert cp2.is_done("a/b/c")


def test_force_ignores_checkpoint(tmp_path):
    cp1 = Checkpoint(tmp_path)
    cp1.mark_done("x/y")
    cp2 = Checkpoint(tmp_path, force=True)
    assert not cp2.is_done("x/y")


def test_make_key():
    assert make_key("proj", "repos", "id") == "proj/repos/id"


def test_jsonl_format(tmp_path):
    cp = Checkpoint(tmp_path)
    cp.mark_done("a")
    cp.mark_done("b", count=10)
    lines = (tmp_path / ".checkpoints.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2
    rec = json.loads(lines[1])
    assert rec["key"] == "b"
    assert rec["count"] == 10


def test_no_duplicate_writes(tmp_path):
    cp = Checkpoint(tmp_path)
    cp.mark_done("x")
    cp.mark_done("x")  # second call should be no-op
    lines = (tmp_path / ".checkpoints.jsonl").read_text().strip().split("\n")
    assert len(lines) == 1
