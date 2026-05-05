"""Unit tests for auth module."""

import base64
import pytest
from unittest.mock import MagicMock, patch

from ado_backup.auth import build_request_headers, AuthError


def _cfg(pat=None, use_az_cli=False, use_service_principal=False, client_id=None, tenant_id=None, client_secret=None):
    cfg = MagicMock()
    cfg.pat = pat
    cfg.use_az_cli = use_az_cli
    cfg.use_service_principal = use_service_principal
    cfg.client_id = client_id
    cfg.tenant_id = tenant_id
    cfg.client_secret = client_secret
    return cfg


def test_pat_produces_basic_auth():
    cfg = _cfg(pat="mytoken")
    headers = build_request_headers(cfg)
    assert "Authorization" in headers
    auth = headers["Authorization"]
    assert auth.startswith("Basic ")
    decoded = base64.b64decode(auth[6:]).decode()
    assert decoded == ":mytoken"


def test_no_credentials_raises():
    cfg = _cfg()
    # Patch az CLI to fail
    with patch("ado_backup.auth._try_az_cli", return_value=None):
        with pytest.raises(AuthError):
            build_request_headers(cfg)


def test_az_cli_used_when_no_pat():
    cfg = _cfg()
    fake_token = "az-cli-bearer-token"
    with patch("ado_backup.auth._try_az_cli", return_value=fake_token):
        headers = build_request_headers(cfg)
    assert headers["Authorization"] == f"Bearer {fake_token}"


def test_pat_token_not_in_plain_text():
    """Ensure the raw PAT never appears as a plain string in Authorization value."""
    secret = "my-super-secret-pat"
    cfg = _cfg(pat=secret)
    headers = build_request_headers(cfg)
    # The Authorization value should be Base64-encoded, not plain
    assert secret not in headers["Authorization"]
