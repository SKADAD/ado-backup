"""Organization-level exporters."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError

log = logging.getLogger(__name__)


def export_organization(client: ADOClient, org_dir: Path, manifest) -> None:
    org_dir.mkdir(parents=True, exist_ok=True)

    _export_org_metadata(client, org_dir, manifest)
    _export_projects(client, org_dir, manifest)
    _export_users_and_groups(client, org_dir, manifest)
    _export_service_connections(client, org_dir, manifest)
    _export_agent_pools(client, org_dir, manifest)
    _export_extensions(client, org_dir, manifest)


def _write_json(path: Path, data) -> int:
    text = json.dumps(data, indent=2, default=str)
    path.write_text(text)
    return len(text.encode())


def _export_org_metadata(client: ADOClient, org_dir: Path, manifest) -> None:
    t0 = time.monotonic()
    out = org_dir / "organization.json"
    try:
        url = client._dev_url("_apis/connectionData")
        data = client.get(url)
        _write_json(out, {
            "organization": client._org,
            "instanceId": data.get("instanceId"),
            "deploymentType": data.get("deploymentType"),
            "locationServiceData": data.get("locationServiceData"),
        })
        manifest.record_org_item("organization-metadata", status="ok")
    except ADOError as exc:
        log.warning("Could not fetch org metadata: %s", exc)
        manifest.record_org_item("organization-metadata", status="error", reason=str(exc))


def _export_projects(client: ADOClient, org_dir: Path, manifest) -> None:
    t0 = time.monotonic()
    out = org_dir / "projects.json"
    try:
        projects = client.list_projects()
        _write_json(out, projects)
        manifest.record_org_item("projects-list", status="ok", count=len(projects))
    except ADOError as exc:
        log.warning("Could not fetch projects list: %s", exc)
        manifest.record_org_item("projects-list", status="error", reason=str(exc))


def _export_users_and_groups(client: ADOClient, org_dir: Path, manifest) -> None:
    # Users via Graph API
    try:
        url = client._vssps_url("_apis/graph/users")
        users = client.get_all(url, {"subjectTypes": "msa,aad,svc,imp"})
        _write_json(org_dir / "users.json", users)
        manifest.record_org_item("users", status="ok", count=len(users))
    except ADOError as exc:
        log.warning("Could not fetch users: %s", exc)
        manifest.record_org_item("users", status="error", reason=str(exc))

    # Groups
    try:
        url = client._vssps_url("_apis/graph/groups")
        groups = client.get_all(url)
        _write_json(org_dir / "groups.json", groups)
        manifest.record_org_item("groups", status="ok", count=len(groups))
    except ADOError as exc:
        log.warning("Could not fetch groups: %s", exc)
        manifest.record_org_item("groups", status="error", reason=str(exc))


def _export_service_connections(client: ADOClient, org_dir: Path, manifest) -> None:
    try:
        url = client._dev_url("_apis/serviceendpoint/endpoints")
        data = client.get(url)
        endpoints = data.get("value", [])
        # Strip secrets — they are not retrievable anyway, but be explicit
        safe_endpoints = []
        for ep in endpoints:
            safe_ep = {k: v for k, v in ep.items() if k != "authorization"}
            safe_ep["_note"] = "Secret/authorization data is not included — ADO does not expose it."
            safe_endpoints.append(safe_ep)
        _write_json(org_dir / "service-connections.json", safe_endpoints)
        manifest.record_org_item("service-connections", status="ok", count=len(safe_endpoints))
    except ADOError as exc:
        log.warning("Could not fetch service connections: %s", exc)
        manifest.record_org_item("service-connections", status="error", reason=str(exc))


def _export_agent_pools(client: ADOClient, org_dir: Path, manifest) -> None:
    try:
        url = client._dev_url("_apis/distributedtask/pools")
        data = client.get(url)
        pools = data.get("value", [])
        _write_json(org_dir / "agent-pools.json", pools)
        manifest.record_org_item("agent-pools", status="ok", count=len(pools))
    except ADOError as exc:
        log.warning("Could not fetch agent pools: %s", exc)
        manifest.record_org_item("agent-pools", status="error", reason=str(exc))


def _export_extensions(client: ADOClient, org_dir: Path, manifest) -> None:
    try:
        url = f"https://extmgmt.dev.azure.com/{client._org}/_apis/extensionmanagement/installedextensions"
        data = client.get(url)
        extensions = data.get("value", [])
        _write_json(org_dir / "extensions.json", extensions)
        manifest.record_org_item("extensions", status="ok", count=len(extensions))
    except ADOError as exc:
        log.warning("Could not fetch extensions: %s", exc)
        manifest.record_org_item("extensions", status="error", reason=str(exc))
