"""Pipelines exporter: build/release/yaml defs, variable groups, task groups, environments, runs."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError
from ado_backup.checkpoint import Checkpoint, make_key

log = logging.getLogger(__name__)


def export_pipelines(
    client: ADOClient,
    project: str,
    project_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    runs_per_pipeline: int = 200,
    include_logs: bool = False,
) -> None:
    pip_dir = project_dir / "pipelines"
    pip_dir.mkdir(parents=True, exist_ok=True)

    _export_build_definitions(client, project, pip_dir, manifest, checkpoint)
    _export_release_definitions(client, project, pip_dir, manifest, checkpoint)
    _export_yaml_pipelines(client, project, pip_dir, manifest, checkpoint, runs_per_pipeline, include_logs)
    _export_variable_groups(client, project, pip_dir, manifest, checkpoint)
    _export_task_groups(client, project, pip_dir, manifest, checkpoint)
    _export_deployment_groups(client, project, pip_dir, manifest, checkpoint)
    _export_environments(client, project, pip_dir, manifest, checkpoint)


def _export_build_definitions(
    client: ADOClient, project: str, pip_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "build-defs")
    builds_dir = pip_dir / "builds"
    builds_dir.mkdir(exist_ok=True)
    if checkpoint.is_done(key):
        return
    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/build/definitions")
        data = client.get(url, {"$top": "2000", "includeAllProperties": "true"})
        defs = data.get("value", [])

        # Fetch full detail for each definition
        for d in defs:
            def_id = d["id"]
            detail_url = client._dev_url(f"{project}/_apis/build/definitions/{def_id}")
            try:
                detail = client.get(detail_url)
                _write_json(builds_dir / f"{def_id}.json", detail)
            except ADOError as exc:
                log.warning("Build def %s detail failed: %s", def_id, exc)
                _write_json(builds_dir / f"{def_id}.json", d)

        _write_json(builds_dir / "index.json", [{"id": d["id"], "name": d.get("name")} for d in defs])
        checkpoint.mark_done(key, count=len(defs))
        manifest.record_item(project, "build-definitions", status="ok", count=len(defs), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.error("Could not fetch build definitions for %s: %s", project, exc)
        manifest.record_item(project, "build-definitions", status="error", reason=str(exc))


def _export_release_definitions(
    client: ADOClient, project: str, pip_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "release-defs")
    releases_dir = pip_dir / "releases"
    releases_dir.mkdir(exist_ok=True)
    if checkpoint.is_done(key):
        return
    t0 = time.monotonic()
    try:
        url = f"https://vsrm.dev.azure.com/{client._org}/{project}/_apis/release/definitions"
        data = client.get(url, {"$top": "2000", "$expand": "environments,artifacts,triggers,variables"})
        defs = data.get("value", [])

        for d in defs:
            def_id = d["id"]
            detail_url = f"https://vsrm.dev.azure.com/{client._org}/{project}/_apis/release/definitions/{def_id}"
            try:
                detail = client.get(detail_url)
                _write_json(releases_dir / f"{def_id}.json", detail)
            except ADOError as exc:
                log.warning("Release def %s detail failed: %s", def_id, exc)
                _write_json(releases_dir / f"{def_id}.json", d)

        _write_json(releases_dir / "index.json", [{"id": d["id"], "name": d.get("name")} for d in defs])
        checkpoint.mark_done(key, count=len(defs))
        manifest.record_item(project, "release-definitions", status="ok", count=len(defs), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.warning("Could not fetch release definitions for %s: %s", project, exc)
        manifest.record_item(project, "release-definitions", status="skipped", reason=str(exc))


def _export_yaml_pipelines(
    client: ADOClient,
    project: str,
    pip_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    runs_per_pipeline: int,
    include_logs: bool,
) -> None:
    key = make_key(project, "yaml-pipelines")
    yaml_dir = pip_dir / "yaml"
    yaml_dir.mkdir(exist_ok=True)
    runs_dir = pip_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    if checkpoint.is_done(key):
        return
    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/pipelines")
        data = client.get(url, {"$top": "500"})
        pipelines = data.get("value", [])

        for p in pipelines:
            pid = p["id"]
            detail_url = client._dev_url(f"{project}/_apis/pipelines/{pid}")
            try:
                detail = client.get(detail_url)
                _write_json(yaml_dir / f"{pid}.json", detail)
            except ADOError as exc:
                log.warning("Pipeline %s detail failed: %s", pid, exc)
                _write_json(yaml_dir / f"{pid}.json", p)

            # Run history
            runs_key = make_key(project, "pipeline-runs", str(pid))
            if not checkpoint.is_done(runs_key):
                _export_pipeline_runs(client, project, pid, runs_dir, runs_per_pipeline, include_logs)
                checkpoint.mark_done(runs_key)

        _write_json(yaml_dir / "index.json", [{"id": p["id"], "name": p.get("name")} for p in pipelines])
        checkpoint.mark_done(key, count=len(pipelines))
        manifest.record_item(project, "yaml-pipelines", status="ok", count=len(pipelines), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.error("Could not fetch YAML pipelines for %s: %s", project, exc)
        manifest.record_item(project, "yaml-pipelines", status="error", reason=str(exc))


def _export_pipeline_runs(
    client: ADOClient,
    project: str,
    pipeline_id: int,
    runs_dir: Path,
    runs_per_pipeline: int,
    include_logs: bool,
) -> None:
    try:
        url = client._dev_url(f"{project}/_apis/pipelines/{pipeline_id}/runs")
        data = client.get(url, {"$top": str(runs_per_pipeline)})
        runs = data.get("value", [])
        # Keep metadata only (logs are large)
        lightweight = [
            {
                "id": r.get("id"),
                "result": r.get("result"),
                "state": r.get("state"),
                "createdDate": r.get("createdDate"),
                "finishedDate": r.get("finishedDate"),
                "pipeline": r.get("pipeline", {}).get("name"),
                "trigger": r.get("triggerInfo"),
            }
            for r in runs
        ]
        _write_json(runs_dir / f"pipeline-{pipeline_id}-runs.json", lightweight)

        if include_logs:
            _download_run_logs(client, project, pipeline_id, runs, runs_dir)
    except ADOError as exc:
        log.warning("Could not fetch runs for pipeline %s: %s", pipeline_id, exc)


def _download_run_logs(client, project, pipeline_id, runs, runs_dir):
    logs_dir = runs_dir / f"pipeline-{pipeline_id}-logs"
    logs_dir.mkdir(exist_ok=True)
    for run in runs[:50]:  # limit to avoid huge downloads
        run_id = run.get("id")
        if not run_id:
            continue
        log_path = logs_dir / f"run-{run_id}.json"
        if log_path.exists():
            continue
        try:
            url = client._dev_url(f"{project}/_apis/pipelines/{pipeline_id}/runs/{run_id}/logs")
            data = client.get(url)
            _write_json(log_path, data)
        except ADOError as exc:
            log.debug("Could not fetch logs for run %s: %s", run_id, exc)


def _export_variable_groups(
    client: ADOClient, project: str, pip_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "variable-groups")
    vg_dir = pip_dir / "variable-groups"
    vg_dir.mkdir(exist_ok=True)
    if checkpoint.is_done(key):
        return
    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/distributedtask/variablegroups")
        data = client.get(url, {"groupType": "Vsts", "$top": "1000"})
        groups = data.get("value", [])

        # Redact secret variable values — they cannot be retrieved anyway
        safe_groups = []
        for vg in groups:
            safe_vg = dict(vg)
            safe_vars = {}
            for vname, vdata in (vg.get("variables") or {}).items():
                if vdata.get("isSecret"):
                    safe_vars[vname] = {"value": None, "isSecret": True}
                else:
                    safe_vars[vname] = vdata
            safe_vg["variables"] = safe_vars
            safe_vg["_note"] = "Secret variable values are not retrievable via the API."
            safe_groups.append(safe_vg)

        _write_json(vg_dir / "variable-groups.json", safe_groups)
        checkpoint.mark_done(key, count=len(safe_groups))
        manifest.record_item(project, "variable-groups", status="ok", count=len(safe_groups), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.warning("Could not fetch variable groups for %s: %s", project, exc)
        manifest.record_item(project, "variable-groups", status="error", reason=str(exc))


def _export_task_groups(
    client: ADOClient, project: str, pip_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "task-groups")
    tg_dir = pip_dir / "task-groups"
    tg_dir.mkdir(exist_ok=True)
    if checkpoint.is_done(key):
        return
    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/distributedtask/taskgroups")
        data = client.get(url)
        groups = data.get("value", [])
        _write_json(tg_dir / "task-groups.json", groups)
        checkpoint.mark_done(key, count=len(groups))
        manifest.record_item(project, "task-groups", status="ok", count=len(groups), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.warning("Could not fetch task groups for %s: %s", project, exc)
        manifest.record_item(project, "task-groups", status="error", reason=str(exc))


def _export_deployment_groups(
    client: ADOClient, project: str, pip_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "deployment-groups")
    dg_dir = pip_dir / "deployment-groups"
    dg_dir.mkdir(exist_ok=True)
    if checkpoint.is_done(key):
        return
    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/distributedtask/deploymentgroups")
        data = client.get(url)
        groups = data.get("value", [])
        _write_json(dg_dir / "deployment-groups.json", groups)
        checkpoint.mark_done(key, count=len(groups))
        manifest.record_item(project, "deployment-groups", status="ok", count=len(groups), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.warning("Could not fetch deployment groups for %s: %s", project, exc)
        manifest.record_item(project, "deployment-groups", status="error", reason=str(exc))


def _export_environments(
    client: ADOClient, project: str, pip_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "environments")
    env_dir = pip_dir / "environments"
    env_dir.mkdir(exist_ok=True)
    if checkpoint.is_done(key):
        return
    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/pipelines/environments")
        data = client.get(url)
        envs = data.get("value", [])
        _write_json(env_dir / "environments.json", envs)
        checkpoint.mark_done(key, count=len(envs))
        manifest.record_item(project, "environments", status="ok", count=len(envs), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.warning("Could not fetch environments for %s: %s", project, exc)
        manifest.record_item(project, "environments", status="error", reason=str(exc))


def _write_json(path: Path, data) -> int:
    text = json.dumps(data, indent=2, default=str)
    path.write_text(text)
    return len(text.encode())
