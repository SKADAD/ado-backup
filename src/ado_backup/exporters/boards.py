"""Boards exporter: dashboards, team configs, process templates."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError
from ado_backup.checkpoint import Checkpoint, make_key

log = logging.getLogger(__name__)


def export_boards(
    client: ADOClient,
    project: str,
    project_dir: Path,
    manifest,
    checkpoint: Checkpoint,
) -> None:
    _export_dashboards(client, project, project_dir, manifest, checkpoint)
    _export_teams_config(client, project, project_dir, manifest, checkpoint)
    _export_process(client, project, project_dir, manifest, checkpoint)


def _export_dashboards(
    client: ADOClient, project: str, project_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "dashboards")
    if checkpoint.is_done(key):
        return
    dash_dir = project_dir / "dashboards"
    dash_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/dashboard/dashboards")
        data = client.get(url)
        dashboards = data.get("dashboardEntries", data.get("value", []))

        for dash in dashboards:
            dash_id = dash.get("id", "")
            if dash_id:
                detail_url = client._dev_url(f"{project}/_apis/dashboard/dashboards/{dash_id}")
                try:
                    detail = client.get(detail_url)
                    _write_json(dash_dir / f"{dash_id}.json", detail)
                except ADOError:
                    _write_json(dash_dir / f"{dash_id}.json", dash)

        _write_json(dash_dir / "index.json", dashboards)
        checkpoint.mark_done(key, count=len(dashboards))
        manifest.record_item(project, "dashboards", status="ok", count=len(dashboards), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.warning("Could not fetch dashboards for %s: %s", project, exc)
        manifest.record_item(project, "dashboards", status="error", reason=str(exc))


def _export_teams_config(
    client: ADOClient, project: str, project_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "teams-config")
    if checkpoint.is_done(key):
        return
    teams_dir = project_dir / "teams"
    teams_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/teams")
        data = client.get(url, {"$top": "500"})
        teams = data.get("value", [])

        for team in teams:
            team_id = team.get("id", "")
            team_name = _safe_name(team.get("name", team_id))
            team_dir = teams_dir / team_name
            team_dir.mkdir(exist_ok=True)
            _write_json(team_dir / "team.json", team)

            # Team members
            try:
                members_url = client._dev_url(f"{project}/_apis/teams/{team_id}/members")
                members = client.get(members_url, {"$top": "500"})
                _write_json(team_dir / "members.json", members)
            except ADOError as exc:
                log.debug("Could not fetch members for team %s: %s", team_name, exc)

            # Team iterations
            try:
                iter_url = client._dev_url(f"{project}/{team_id}/_apis/work/teamsettings/iterations")
                iterations = client.get(iter_url)
                _write_json(team_dir / "iterations.json", iterations)
            except ADOError:
                pass

            # Board columns + swim lanes
            try:
                boards_url = client._dev_url(f"{project}/{team_id}/_apis/work/boards")
                boards_data = client.get(boards_url)
                boards = boards_data.get("value", [])
                for board in boards:
                    board_id = board.get("id", "")
                    board_name = _safe_name(board.get("name", board_id))
                    board_url = client._dev_url(f"{project}/{team_id}/_apis/work/boards/{board_id}")
                    try:
                        board_detail = client.get(board_url)
                        _write_json(team_dir / f"board-{board_name}.json", board_detail)
                    except ADOError:
                        pass
            except ADOError:
                pass

            # Team settings
            try:
                settings_url = client._dev_url(f"{project}/{team_id}/_apis/work/teamsettings")
                settings = client.get(settings_url)
                _write_json(team_dir / "settings.json", settings)
            except ADOError:
                pass

        checkpoint.mark_done(key, count=len(teams))
        manifest.record_item(project, "teams", status="ok", count=len(teams), duration=time.monotonic() - t0)
    except ADOError as exc:
        log.warning("Could not fetch teams for %s: %s", project, exc)
        manifest.record_item(project, "teams", status="error", reason=str(exc))


def _export_process(
    client: ADOClient, project: str, project_dir: Path, manifest, checkpoint: Checkpoint
) -> None:
    key = make_key(project, "process")
    if checkpoint.is_done(key):
        return
    proc_dir = project_dir / "process"
    proc_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        # Get the project's process template reference
        proj_url = client._dev_url(f"_apis/projects/{project}")
        proj = client.get(proj_url, {"includeCapabilities": "true"})
        capabilities = proj.get("capabilities", {})
        process_template = capabilities.get("processTemplate", {})
        template_name = process_template.get("templateName", "")

        _write_json(proc_dir / "process-reference.json", process_template)

        # System processes (Agile/Scrum/CMMI/Basic) — just record the name
        system_processes = {"Agile", "Scrum", "CMMI", "Basic"}
        if template_name in system_processes:
            manifest.record_item(
                project, "process", status="ok",
                reason=f"System process '{template_name}' — no inherited customization to export.",
                duration=time.monotonic() - t0,
            )
            checkpoint.mark_done(key)
            return

        # Inherited process — export work item types, fields, states, rules
        processes_url = client._dev_url("_apis/work/processes")
        processes_data = client.get(processes_url)
        processes = processes_data.get("value", [])

        # Find matching process
        for proc in processes:
            if proc.get("name") == template_name or proc.get("typeId") == process_template.get("templateTypeId"):
                proc_id = proc.get("typeId")
                _write_json(proc_dir / "process.json", proc)

                # Work item types
                wit_url = client._dev_url(f"_apis/work/processes/{proc_id}/workitemtypes")
                try:
                    wits = client.get(wit_url)
                    _write_json(proc_dir / "work-item-types.json", wits)
                except ADOError:
                    pass
                break

        checkpoint.mark_done(key)
        manifest.record_item(project, "process", status="ok", duration=time.monotonic() - t0)
    except ADOError as exc:
        log.warning("Could not fetch process for %s: %s", project, exc)
        manifest.record_item(project, "process", status="error", reason=str(exc))


def _safe_name(name: str) -> str:
    name = name.strip()
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    while ".." in name:
        name = name.replace("..", "_")
    name = name.strip(".")
    return name[:200] or "unnamed"


def _write_json(path: Path, data) -> int:
    text = json.dumps(data, indent=2, default=str)
    path.write_text(text)
    return len(text.encode())
