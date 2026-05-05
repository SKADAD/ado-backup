"""Pipelines restorer: variable groups, task groups, build/release/yaml definitions, environments."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from ado_backup.client import ADOClient, ADOError

log = logging.getLogger(__name__)


def restore_pipelines(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    report,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    pip_dir = project_backup_dir / "pipelines"
    if not pip_dir.exists():
        report.record(source_project, "pipelines", status="skipped", reason="No pipelines directory in backup")
        return

    # Order matters: variable groups and task groups must exist before definitions reference them
    vg_map = _restore_variable_groups(client, source_project, target_project, pip_dir, report, force, dry_run)
    _restore_task_groups(client, source_project, target_project, pip_dir, report, force, dry_run)
    _restore_environments(client, source_project, target_project, pip_dir, report, force, dry_run)
    _restore_build_definitions(client, source_project, target_project, pip_dir, report, force, dry_run)
    _restore_release_definitions(client, source_project, target_project, pip_dir, report, force, dry_run)
    _restore_yaml_pipelines(client, source_project, target_project, pip_dir, report, force, dry_run)


def _restore_variable_groups(
    client: ADOClient,
    source_project: str,
    target_project: str,
    pip_dir: Path,
    report,
    force: bool,
    dry_run: bool,
) -> dict[int, int]:
    """Restore variable groups. Returns old_id → new_id map."""
    vg_path = pip_dir / "variable-groups" / "variable-groups.json"
    if not vg_path.exists():
        return {}

    try:
        vgs = json.loads(vg_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    id_map: dict[int, int] = {}
    t0 = time.monotonic()

    # Fetch existing VGs to detect conflicts
    existing_vgs: dict[str, int] = {}
    try:
        url = client._dev_url(f"{target_project}/_apis/distributedtask/variablegroups")
        existing = client.get(url, {"$top": "1000"}).get("value", [])
        existing_vgs = {v["name"]: v["id"] for v in existing}
    except ADOError:
        pass

    created = 0
    for vg in vgs:
        name = vg.get("name", "")
        if name in existing_vgs and not force:
            log.debug("Variable group %s already exists; skipping", name)
            id_map[vg.get("id", 0)] = existing_vgs[name]
            continue

        if dry_run:
            log.info("[dry-run] Would restore variable group %s", name)
            created += 1
            continue

        body = {
            "name": name,
            "description": vg.get("description", ""),
            "type": vg.get("type", "Vsts"),
            "variables": vg.get("variables", {}),
        }
        try:
            url = client._dev_url(f"{target_project}/_apis/distributedtask/variablegroups")
            result = client.post(url, body)
            id_map[vg.get("id", 0)] = result.get("id", 0)
            created += 1
        except ADOError as exc:
            log.warning("Could not restore variable group %s: %s", name, exc)

    if created or vgs:
        report.record(
            source_project, "variable-groups", status="ok", count=created,
            duration=time.monotonic() - t0,
            reason="Secret values not restored — must be re-entered manually." if vgs else "",
        )
    return id_map


def _restore_task_groups(
    client: ADOClient,
    source_project: str,
    target_project: str,
    pip_dir: Path,
    report,
    force: bool,
    dry_run: bool,
) -> None:
    tg_path = pip_dir / "task-groups" / "task-groups.json"
    if not tg_path.exists():
        return
    try:
        tgs = json.loads(tg_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    t0 = time.monotonic()
    created = 0
    for tg in tgs:
        name = tg.get("name", "")
        if dry_run:
            log.info("[dry-run] Would restore task group %s", name)
            created += 1
            continue
        # Strip server-managed fields
        body = {k: v for k, v in tg.items() if k not in ("id", "revision", "createdBy", "createdOn", "modifiedBy", "modifiedOn")}
        try:
            url = client._dev_url(f"{target_project}/_apis/distributedtask/taskgroups")
            client.post(url, body)
            created += 1
        except ADOError as exc:
            log.warning("Could not restore task group %s: %s", name, exc)

    report.record(source_project, "task-groups", status="ok", count=created, duration=time.monotonic() - t0)


def _restore_environments(
    client: ADOClient,
    source_project: str,
    target_project: str,
    pip_dir: Path,
    report,
    force: bool,
    dry_run: bool,
) -> None:
    env_path = pip_dir / "environments" / "environments.json"
    if not env_path.exists():
        return
    try:
        envs = json.loads(env_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    # Fetch existing environments
    existing_envs: set[str] = set()
    try:
        url = client._dev_url(f"{target_project}/_apis/pipelines/environments")
        existing_envs = {e["name"] for e in client.get(url).get("value", [])}
    except ADOError:
        pass

    t0 = time.monotonic()
    created = 0
    for env in envs:
        name = env.get("name", "")
        if name in existing_envs and not force:
            continue
        if dry_run:
            log.info("[dry-run] Would restore environment %s", name)
            created += 1
            continue
        body = {"name": name, "description": env.get("description", "")}
        try:
            url = client._dev_url(f"{target_project}/_apis/pipelines/environments")
            client.post(url, body)
            created += 1
        except ADOError as exc:
            log.warning("Could not restore environment %s: %s", name, exc)

    if created:
        report.record(source_project, "environments", status="ok", count=created, duration=time.monotonic() - t0)


def _restore_build_definitions(
    client: ADOClient,
    source_project: str,
    target_project: str,
    pip_dir: Path,
    report,
    force: bool,
    dry_run: bool,
) -> None:
    builds_dir = pip_dir / "builds"
    if not builds_dir.exists():
        return

    def_files = [f for f in builds_dir.glob("*.json") if f.stem.isdigit()]
    if not def_files:
        return

    # Existing definitions
    existing: set[str] = set()
    try:
        url = client._dev_url(f"{target_project}/_apis/build/definitions")
        existing = {d["name"] for d in client.get(url).get("value", [])}
    except ADOError:
        pass

    t0 = time.monotonic()
    created = 0
    for def_file in def_files:
        try:
            defn = json.loads(def_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        name = defn.get("name", "")
        if name in existing and not force:
            log.debug("Build definition %s already exists; skipping", name)
            continue

        if dry_run:
            log.info("[dry-run] Would restore build definition %s", name)
            created += 1
            continue

        body = _strip_server_fields_build(defn, target_project)
        try:
            url = client._dev_url(f"{target_project}/_apis/build/definitions")
            client.post(url, body)
            created += 1
        except ADOError as exc:
            log.warning("Could not restore build definition %s: %s", name, exc)

    report.record(source_project, "build-definitions", status="ok", count=created, duration=time.monotonic() - t0)


def _restore_release_definitions(
    client: ADOClient,
    source_project: str,
    target_project: str,
    pip_dir: Path,
    report,
    force: bool,
    dry_run: bool,
) -> None:
    releases_dir = pip_dir / "releases"
    if not releases_dir.exists():
        return

    def_files = [f for f in releases_dir.glob("*.json") if f.stem.isdigit()]
    if not def_files:
        return

    existing: set[str] = set()
    try:
        url = f"https://vsrm.dev.azure.com/{client._org}/{target_project}/_apis/release/definitions"
        existing = {d["name"] for d in client.get(url).get("value", [])}
    except ADOError:
        pass

    t0 = time.monotonic()
    created = 0
    for def_file in def_files:
        try:
            defn = json.loads(def_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        name = defn.get("name", "")
        if name in existing and not force:
            continue

        if dry_run:
            log.info("[dry-run] Would restore release definition %s", name)
            created += 1
            continue

        body = _strip_server_fields_release(defn, target_project)
        try:
            url = f"https://vsrm.dev.azure.com/{client._org}/{target_project}/_apis/release/definitions"
            client.post(url, body)
            created += 1
        except ADOError as exc:
            log.warning("Could not restore release definition %s: %s", name, exc)

    report.record(source_project, "release-definitions", status="ok", count=created, duration=time.monotonic() - t0)


def _restore_yaml_pipelines(
    client: ADOClient,
    source_project: str,
    target_project: str,
    pip_dir: Path,
    report,
    force: bool,
    dry_run: bool,
) -> None:
    yaml_dir = pip_dir / "yaml"
    if not yaml_dir.exists():
        return

    def_files = [f for f in yaml_dir.glob("*.json") if f.stem.isdigit()]
    if not def_files:
        return

    existing: set[str] = set()
    try:
        url = client._dev_url(f"{target_project}/_apis/pipelines")
        existing = {p["name"] for p in client.get(url).get("value", [])}
    except ADOError:
        pass

    t0 = time.monotonic()
    created = 0
    skipped_no_yaml = 0
    for def_file in def_files:
        try:
            defn = json.loads(def_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        name = defn.get("name", "")
        # YAML pipelines must reference a YAML file in a repository.
        # The repository must have been restored first.
        config = defn.get("configuration", {})
        if config.get("type") != "yaml" or not config.get("path"):
            skipped_no_yaml += 1
            continue

        if name in existing and not force:
            continue

        if dry_run:
            log.info("[dry-run] Would restore YAML pipeline %s", name)
            created += 1
            continue

        repo_ref = config.get("repository", {})
        body = {
            "name": name,
            "configuration": {
                "type": "yaml",
                "path": config.get("path"),
                "repository": {
                    "id": repo_ref.get("id"),
                    "type": repo_ref.get("type", "azureReposGit"),
                },
            },
        }
        try:
            url = client._dev_url(f"{target_project}/_apis/pipelines")
            client.post(url, body)
            created += 1
        except ADOError as exc:
            log.warning("Could not restore YAML pipeline %s: %s", name, exc)

    reason = (f"{skipped_no_yaml} pipeline(s) skipped — no YAML path (classic pipelines; restore from build definitions)."
              if skipped_no_yaml else "")
    report.record(source_project, "yaml-pipelines", status="ok", count=created,
                  duration=time.monotonic() - t0, reason=reason)


def _strip_server_fields_build(defn: dict, target_project: str) -> dict:
    """Remove server-managed fields from a build definition before POSTing."""
    skip = {"id", "revision", "createdDate", "queueStatus", "uri", "url", "_links", "authoredBy", "latestBuild", "latestCompletedBuild", "metrics"}
    body = {k: v for k, v in defn.items() if k not in skip}
    # Update project reference
    body["project"] = {"name": target_project}
    return body


def _strip_server_fields_release(defn: dict, target_project: str) -> dict:
    skip = {"id", "revision", "createdOn", "modifiedOn", "createdBy", "modifiedBy", "url", "_links", "lastRelease", "source"}
    body = {k: v for k, v in defn.items() if k not in skip}
    return body
