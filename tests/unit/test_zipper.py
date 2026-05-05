"""Unit tests for zipper module."""

import zipfile
from pathlib import Path

from ado_backup.zipper import zip_directory, verify_archive


def _make_tree(base: Path):
    (base / "a").mkdir()
    (base / "a" / "file1.txt").write_text("hello")
    (base / "a" / "file2.json").write_text('{"key": "value"}')
    (base / "b").mkdir()
    (base / "b" / "nested").mkdir()
    (base / "b" / "nested" / "deep.txt").write_text("deep content")


def test_zip_creates_archive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_tree(src)
    dest = tmp_path / "out.zip"
    zip_directory(src, dest)
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_zip_contains_all_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_tree(src)
    dest = tmp_path / "out.zip"
    zip_directory(src, dest)
    with zipfile.ZipFile(dest) as zf:
        names = {Path(n).name for n in zf.namelist()}
    assert "file1.txt" in names
    assert "file2.json" in names
    assert "deep.txt" in names


def test_verify_good_archive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_tree(src)
    dest = tmp_path / "out.zip"
    zip_directory(src, dest)
    ok, errors = verify_archive(dest)
    assert ok
    assert not errors


def test_verify_missing_archive(tmp_path):
    ok, errors = verify_archive(tmp_path / "nonexistent.zip")
    assert not ok
    assert errors


def test_verify_corrupt_archive(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip file")
    ok, errors = verify_archive(bad)
    assert not ok
    assert errors
