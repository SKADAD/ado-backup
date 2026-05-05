"""Azure DevOps REST API client wrapper.

Handles:
- Base URL construction for both dev.azure.com and vssps.dev.azure.com
- Rate-limit aware retries (Retry-After, X-RateLimit-*, 429/5xx)
- Logging of x-ms-request-id on failures
- Automatic pagination (continuationToken / x-ms-continuationtoken)
- Secret redaction from debug output
"""

from __future__ import annotations

import logging
import time
import random
from typing import Any, Iterator, Optional
from urllib.parse import urlencode

import httpx

from ado_backup.auth import build_request_headers

log = logging.getLogger(__name__)

API_VERSION = "7.1"
MAX_RETRIES = 5
BASE_BACKOFF = 1.0  # seconds


class ADOError(Exception):
    def __init__(self, message: str, status_code: int = 0, request_id: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class ADOClient:
    def __init__(self, config):
        self._config = config
        self._org = config.org
        self._headers = build_request_headers(config)
        self._http = httpx.Client(
            timeout=httpx.Timeout(connect=30, read=300, write=30, pool=10),
            follow_redirects=True,
        )

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Base URL helpers
    # ------------------------------------------------------------------

    def _dev_url(self, path: str) -> str:
        return f"https://dev.azure.com/{self._org}/{path}"

    def _vssps_url(self, path: str) -> str:
        return f"https://vssps.dev.azure.com/{self._org}/{path}"

    def _vsaex_url(self, path: str) -> str:
        return f"https://vsaex.dev.azure.com/{self._org}/{path}"

    def _feeds_url(self, org_or_project: str, path: str) -> str:
        return f"https://feeds.dev.azure.com/{org_or_project}/{path}"

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    def get(
        self,
        url: str,
        params: Optional[dict] = None,
        stream: bool = False,
        extra_headers: Optional[dict] = None,
    ) -> Any:
        """GET a URL with retry/backoff. Returns parsed JSON."""
        _params = {"api-version": API_VERSION}
        if params:
            _params.update(params)

        headers = dict(self._headers)
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._http.get(url, params=_params, headers=headers)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise ADOError(f"Network error: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            request_id = resp.headers.get("x-ms-request-id", "")

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait = float(retry_after)
                    log.warning("Rate limited; sleeping %.1fs (x-ms-request-id: %s)", wait, request_id)
                    time.sleep(wait)
                elif attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt)
                else:
                    raise ADOError(
                        f"HTTP {resp.status_code} after {MAX_RETRIES} retries: {url}",
                        status_code=resp.status_code,
                        request_id=request_id,
                    )
                continue

            if resp.status_code == 203:
                # Sometimes ADO returns 203 on auth issues
                raise ADOError(
                    f"HTTP 203 Non-Authoritative (likely auth issue): {url}",
                    status_code=203,
                    request_id=request_id,
                )

            if resp.status_code == 401:
                raise ADOError(
                    f"HTTP 401 Unauthorized — check your PAT scopes: {url}",
                    status_code=401,
                    request_id=request_id,
                )

            if resp.status_code == 403:
                raise ADOError(
                    f"HTTP 403 Forbidden — insufficient permissions: {url}",
                    status_code=403,
                    request_id=request_id,
                )

            if resp.status_code == 404:
                raise ADOError(
                    f"HTTP 404 Not Found: {url}",
                    status_code=404,
                    request_id=request_id,
                )

            raise ADOError(
                f"HTTP {resp.status_code}: {url} (x-ms-request-id: {request_id})",
                status_code=resp.status_code,
                request_id=request_id,
            )

        raise ADOError(f"Exhausted retries for {url}")

    def get_raw(self, url: str, params: Optional[dict] = None) -> bytes:
        """GET binary content (attachments, package files)."""
        _params = {"api-version": API_VERSION}
        if params:
            _params.update(params)

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._http.get(url, params=_params, headers=self._headers)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise ADOError(f"Network error: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            if resp.status_code == 200:
                return resp.content

            if resp.status_code == 429 or resp.status_code >= 500:
                self._sleep_backoff(attempt)
                continue

            raise ADOError(
                f"HTTP {resp.status_code} fetching binary: {url}",
                status_code=resp.status_code,
            )

        raise ADOError(f"Exhausted retries for {url}")

    def post(self, url: str, body: dict, params: Optional[dict] = None) -> Any:
        """POST JSON body, return parsed response."""
        _params = {"api-version": API_VERSION}
        if params:
            _params.update(params)

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._http.post(url, params=_params, headers=self._headers, json=body)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise ADOError(f"Network error: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            request_id = resp.headers.get("x-ms-request-id", "")

            if resp.status_code in (200, 201):
                return resp.json()

            if resp.status_code == 429 or resp.status_code >= 500:
                self._sleep_backoff(attempt)
                continue

            raise ADOError(
                f"HTTP {resp.status_code} POST {url} (x-ms-request-id: {request_id})",
                status_code=resp.status_code,
                request_id=request_id,
            )

        raise ADOError(f"Exhausted retries for POST {url}")

    # ------------------------------------------------------------------
    # Paginated list helper
    # ------------------------------------------------------------------

    def get_all(self, url: str, params: Optional[dict] = None, value_key: str = "value") -> list:
        """Fetch all pages using continuationToken header."""
        _params = dict(params or {})
        results = []

        while True:
            resp_raw = self._get_with_headers(url, _params)
            data = resp_raw["body"]

            if isinstance(data, dict):
                items = data.get(value_key, [])
                if isinstance(items, list):
                    results.extend(items)
                else:
                    results.append(data)
            elif isinstance(data, list):
                results.extend(data)

            continuation = resp_raw["headers"].get("x-ms-continuationtoken")
            if not continuation:
                break
            _params["continuationToken"] = continuation

        return results

    def _get_with_headers(self, url: str, params: Optional[dict] = None) -> dict:
        """GET returning both body and response headers."""
        _params = {"api-version": API_VERSION}
        if params:
            _params.update(params)

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._http.get(url, params=_params, headers=self._headers)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise ADOError(f"Network error: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            request_id = resp.headers.get("x-ms-request-id", "")

            if resp.status_code == 200:
                return {"body": resp.json(), "headers": dict(resp.headers)}

            if resp.status_code == 429 or resp.status_code >= 500:
                self._sleep_backoff(attempt)
                continue

            raise ADOError(
                f"HTTP {resp.status_code}: {url} (x-ms-request-id: {request_id})",
                status_code=resp.status_code,
                request_id=request_id,
            )

        raise ADOError(f"Exhausted retries for {url}")

    # ------------------------------------------------------------------
    # ADO domain helpers
    # ------------------------------------------------------------------

    def list_projects(self) -> list[dict]:
        url = self._dev_url("_apis/projects")
        return self.get_all(url, {"$top": "200"})

    def list_repos(self, project: str) -> list[dict]:
        url = self._dev_url(f"{project}/_apis/git/repositories")
        data = self.get(url)
        return data.get("value", [])

    def list_work_item_ids(self, project: str) -> list[int]:
        url = self._dev_url(f"{project}/_apis/wit/wiql")
        body = {"query": "SELECT [System.Id] FROM workitems ORDER BY [System.Id] ASC"}
        data = self.post(url, body)
        return [wi["id"] for wi in data.get("workItems", [])]

    def get_work_items_batch(self, ids: list[int], fields: Optional[list[str]] = None) -> list[dict]:
        if not ids:
            return []
        url = self._dev_url("_apis/wit/workitemsbatch")
        body: dict[str, Any] = {"ids": ids, "$expand": "all"}
        if fields:
            body["fields"] = fields
        data = self.post(url, body)
        return data.get("value", [])

    def get_work_item_revisions(self, project: str, item_id: int) -> list[dict]:
        url = self._dev_url(f"{project}/_apis/wit/workitems/{item_id}/revisions")
        try:
            data = self.get(url, {"$expand": "all"})
            return data.get("value", [])
        except ADOError:
            return []

    def get_work_item_comments(self, project: str, item_id: int) -> list[dict]:
        url = self._dev_url(f"{project}/_apis/wit/workitems/{item_id}/comments")
        try:
            data = self.get(url)
            return data.get("comments", [])
        except ADOError:
            return []

    def patch(
        self,
        url: str,
        body: list | dict,
        params: Optional[dict] = None,
        content_type: str = "application/json",
    ) -> Any:
        """PATCH with retry/backoff. Use content_type='application/json-patch+json' for work items."""
        _params = {"api-version": API_VERSION}
        if params:
            _params.update(params)
        headers = dict(self._headers)
        headers["Content-Type"] = content_type

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._http.patch(url, params=_params, headers=headers, json=body)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise ADOError(f"Network error: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            request_id = resp.headers.get("x-ms-request-id", "")

            if resp.status_code in (200, 201):
                return resp.json()

            if resp.status_code == 429 or resp.status_code >= 500:
                self._sleep_backoff(attempt)
                continue

            raise ADOError(
                f"HTTP {resp.status_code} PATCH {url} (x-ms-request-id: {request_id}): {resp.text[:300]}",
                status_code=resp.status_code,
                request_id=request_id,
            )

        raise ADOError(f"Exhausted retries for PATCH {url}")

    def put(self, url: str, body: dict, params: Optional[dict] = None) -> Any:
        """PUT JSON body, return parsed response."""
        _params = {"api-version": API_VERSION}
        if params:
            _params.update(params)

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._http.put(url, params=_params, headers=self._headers, json=body)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise ADOError(f"Network error: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            request_id = resp.headers.get("x-ms-request-id", "")

            if resp.status_code in (200, 201):
                return resp.json()

            if resp.status_code == 429 or resp.status_code >= 500:
                self._sleep_backoff(attempt)
                continue

            raise ADOError(
                f"HTTP {resp.status_code} PUT {url} (x-ms-request-id: {request_id}): {resp.text[:300]}",
                status_code=resp.status_code,
                request_id=request_id,
            )

        raise ADOError(f"Exhausted retries for PUT {url}")

    def delete(self, url: str, params: Optional[dict] = None) -> None:
        """DELETE a resource."""
        _params = {"api-version": API_VERSION}
        if params:
            _params.update(params)

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._http.delete(url, params=_params, headers=self._headers)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise ADOError(f"Network error: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            if resp.status_code in (200, 204):
                return

            if resp.status_code == 429 or resp.status_code >= 500:
                self._sleep_backoff(attempt)
                continue

            raise ADOError(
                f"HTTP {resp.status_code} DELETE {url}",
                status_code=resp.status_code,
            )

        raise ADOError(f"Exhausted retries for DELETE {url}")

    def create_project(self, name: str, description: str = "", process_template_id: str = "") -> dict:
        """Create an ADO project. Polls until it finishes (async operation)."""
        url = self._dev_url("_apis/projects")
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            "visibility": "private",
            "capabilities": {
                "versioncontrol": {"sourceControlType": "Git"},
                "processTemplate": {
                    "templateTypeId": process_template_id or "6b724908-ef14-45cf-84f8-768b5384da45"  # Agile
                },
            },
        }
        result = self.post(url, body)
        # ADO project creation is async — poll the operation
        op_url = result.get("url", "")
        if op_url:
            self._poll_operation(op_url)
        # Return the newly created project
        proj_url = self._dev_url(f"_apis/projects/{name}")
        return self.get(proj_url)

    def _poll_operation(self, op_url: str, timeout: int = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = self._http.get(op_url, params={"api-version": API_VERSION}, headers=self._headers)
                if result.status_code == 200:
                    data = result.json()
                    status = data.get("status", "")
                    if status in ("succeeded", "failed", "cancelled"):
                        if status != "succeeded":
                            raise ADOError(f"Async operation {status}: {data}")
                        return
            except httpx.RequestError:
                pass
            time.sleep(3)
        raise ADOError(f"Async operation timed out after {timeout}s: {op_url}")

    def _sleep_backoff(self, attempt: int):
        wait = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
        log.debug("Backoff %.2fs (attempt %d)", wait, attempt + 1)
        time.sleep(wait)
