"""Unit tests for path sanitization utilities."""

import pytest
from ado_backup.exporters.repositories import _safe_name


@pytest.mark.parametrize("raw,expected", [
    ("normal-repo", "normal-repo"),
    ("repo/with/slashes", "repo_with_slashes"),
    ("repo\\backslash", "repo_backslash"),
    ("has:colon", "has_colon"),
    ('has*star?q"quote<lt>gt|pipe', "has_star_q_quote_lt_gt_pipe"),
    ("...leading-dots", "_.leading-dots"),  # .. collapsed to _, then leading dot stripped
    ("a" * 300, "a" * 200),              # truncate at 200
    ("", "unnamed"),
    ("   ", "unnamed"),                  # whitespace-only
])
def test_safe_name(raw, expected):
    # _safe_name strips leading/trailing dots and truncates
    result = _safe_name(raw)
    assert result == expected


def test_safe_name_no_path_traversal():
    """Ensure we cannot escape the output directory."""
    dangerous = "../../../etc/passwd"
    result = _safe_name(dangerous)
    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result
