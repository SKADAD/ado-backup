"""Unit tests for client retry/backoff logic."""

import pytest
import httpx
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


def _make_response(status_code: int, body: dict = None, headers: dict = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.headers = headers or {}
    resp.content = b""
    return resp


def test_get_success():
    cfg = _make_config()
    client = ADOClient(cfg)
    ok_resp = _make_response(200, {"value": [1, 2, 3]})
    with patch.object(client._http, "get", return_value=ok_resp):
        result = client.get("https://example.com/test")
    assert result == {"value": [1, 2, 3]}


def test_404_raises_ado_error():
    cfg = _make_config()
    client = ADOClient(cfg)
    resp = _make_response(404)
    with patch.object(client._http, "get", return_value=resp):
        with pytest.raises(ADOError) as exc_info:
            client.get("https://example.com/missing")
    assert exc_info.value.status_code == 404


def test_401_raises_with_message():
    cfg = _make_config()
    client = ADOClient(cfg)
    resp = _make_response(401)
    with patch.object(client._http, "get", return_value=resp):
        with pytest.raises(ADOError) as exc_info:
            client.get("https://example.com/auth")
    assert "401" in str(exc_info.value)
    assert "scope" in str(exc_info.value).lower()


def test_429_uses_retry_after(monkeypatch):
    cfg = _make_config()
    client = ADOClient(cfg)
    rate_resp = _make_response(429, headers={"Retry-After": "0.01", "x-ms-request-id": "abc"})
    ok_resp = _make_response(200, {"value": []})
    call_count = 0

    def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return rate_resp if call_count == 1 else ok_resp

    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    with patch.object(client._http, "get", side_effect=fake_get):
        result = client.get("https://example.com/rate")
    assert result == {"value": []}
    assert call_count == 2
    assert any(s > 0 for s in slept)


def test_5xx_retries_with_backoff(monkeypatch):
    cfg = _make_config()
    client = ADOClient(cfg)
    error_resp = _make_response(503)
    ok_resp = _make_response(200, {"ok": True})
    call_count = 0

    def fake_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return error_resp if call_count < 3 else ok_resp

    monkeypatch.setattr("time.sleep", lambda _: None)
    with patch.object(client._http, "get", side_effect=fake_get):
        result = client.get("https://example.com/flaky")
    assert call_count == 3
    assert result == {"ok": True}


def test_exhausted_retries_raises(monkeypatch):
    cfg = _make_config()
    client = ADOClient(cfg)
    error_resp = _make_response(503)
    monkeypatch.setattr("time.sleep", lambda _: None)
    with patch.object(client._http, "get", return_value=error_resp):
        with pytest.raises(ADOError):
            client.get("https://example.com/down")
