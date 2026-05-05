"""Unit tests for new client methods: patch, put, delete."""

import pytest
from unittest.mock import patch, MagicMock

from ado_backup.client import ADOClient, ADOError


def _make_config(org="myorg", pat="token"):
    cfg = MagicMock()
    cfg.org = org
    cfg.pat = pat
    cfg.use_az_cli = False
    cfg.use_service_principal = False
    cfg.client_id = None
    cfg.tenant_id = None
    cfg.client_secret = None
    return cfg


def _make_response(status_code: int, body=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.headers = headers or {}
    resp.text = ""
    return resp


def test_patch_success():
    cfg = _make_config()
    client = ADOClient(cfg)
    ok = _make_response(200, {"id": 42})
    with patch.object(client._http, "patch", return_value=ok):
        result = client.patch("https://example.com/wi/1", [{"op": "add", "path": "/fields/System.Title", "value": "T"}])
    assert result["id"] == 42


def test_patch_uses_custom_content_type():
    cfg = _make_config()
    client = ADOClient(cfg)
    ok = _make_response(200, {"id": 1})
    with patch.object(client._http, "patch", return_value=ok) as mock_patch:
        client.patch("https://x.com", [], content_type="application/json-patch+json")
    called_headers = mock_patch.call_args[1]["headers"]
    assert called_headers["Content-Type"] == "application/json-patch+json"


def test_patch_400_raises():
    cfg = _make_config()
    client = ADOClient(cfg)
    resp = _make_response(400)
    with patch.object(client._http, "patch", return_value=resp):
        with pytest.raises(ADOError) as exc_info:
            client.patch("https://example.com", [])
    assert exc_info.value.status_code == 400


def test_put_success():
    cfg = _make_config()
    client = ADOClient(cfg)
    ok = _make_response(200, {"name": "updated"})
    with patch.object(client._http, "put", return_value=ok):
        result = client.put("https://example.com/resource", {"name": "updated"})
    assert result["name"] == "updated"


def test_delete_204_success():
    cfg = _make_config()
    client = ADOClient(cfg)
    ok = _make_response(204)
    with patch.object(client._http, "delete", return_value=ok):
        client.delete("https://example.com/resource/1")  # should not raise


def test_delete_404_raises():
    cfg = _make_config()
    client = ADOClient(cfg)
    resp = _make_response(404)
    with patch.object(client._http, "delete", return_value=resp):
        with pytest.raises(ADOError):
            client.delete("https://example.com/resource/1")
