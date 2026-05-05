"""Boards restorer: teams and dashboards."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError

log = logging.getLogger(__name__)


def restore_boards(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    report,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, str]:
    """Restore teams and dashboards. Returns old_team_name → new_team_id map."""
    team_map = _restore_teams(client, source_project, target_project, project_backup_dir, report, force, dry_run)
    _restore_dashboards(client, source_project, target_project, project_backup_dir, team_map, report, force, dry_run)
    return team_map


def _restore_teams(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    report,
    force: bool,
    dry_run: bool,
) -> dict[str, str]:
    teams_dir = project_backup_dir / "teams"
    if not teams_dir.exists():
        return {}

    # Fetch existing teams
    existing_teams: dict[str, str] = {}
    try:
        url = client._dev_url(f"{target_project}/_apis/teams")
        existing_teams = {t["name"]: t["id"] for t in client.get(url, {"$top": "500"}).get("value", [])}
    except ADOError:
        pass

    team_map: dict[str, str] = {}
    t0 = time.monotonic()
    created = 0

    for team_dir in sorted(teams_dir.iterdir()):
        if not team_dir.is_dir():
            continue
        team_name = team_dir.name
        team_path = team_dir / "team.json"
        if not team_path.exists():
            continue
        try:
            team = json.loads(team_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if team_name in existing_teams:
            team_map[team_name] = existing_teams[team_name]
            if not force:
                continue

        if dry_run:
            log.info("[dry-run] Would restore team %s/%s", target_project, team_name)
            created += 1
            continue

        try:
            url = client._dev_url(f"{target_project}/_apis/teams")
            result = client.post(url, {"name": team_name, "description": team.get("description", "")})
            team_map[team_name] = result.get("id", "")
            created += 1
        except ADOError as exc:
            # "already exists" is common — treat as warning
            if "already exists" in str(exc).lower() or "400" in str(exc):
                team_map[team_name] = existing_teams.get(team_name, "")
            else:
                log.warning("Could not restore team %s: %s", team_name, exc)

    report.record(source_project, "teams", status="ok", count=created, duration=time.monotonic() - t0)
    return team_map


def _restore_dashboards(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    team_map: dict[str, str],
    report,
    force: bool,
    dry_run: bool,
) -> None:
    dash_dir = project_backup_dir / "dashboards"
    if not dash_dir.exists():
        return

    # Get the default team ID for the target project
    default_team_id = _get_default_team_id(client, target_project)
    if not default_team_id:
        report.record(source_project, "dashboards", status="skipped", reason="Could not determine default team")
        return

    t0 = time.monotonic()
    created = 0

    for dash_file in sorted(dash_dir.glob("*.json")):
        if dash_file.stem == "index":
            continue
        try:
            dash = json.loads(dash_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        name = dash.get("name", "")
        if dry_run:
            log.info("[dry-run] Would restore dashboard %s/%s", target_project, name)
            created += 1
            continue

        body = _strip_dashboard_server_fields(dash)
        try:
            url = client._dev_url(f"{target_project}/{default_team_id}/_apis/dashboard/dashboards")
            client.post(url, body)
            created += 1
        except ADOError as exc:
            log.warning("Could not restore dashboard %s: %s", name, exc)

    report.record(
        source_project, "dashboards", status="ok", count=created,
        duration=time.monotonic() - t0,
        reason="Widget live data references are not restorable." if created else "",
    )


def _get_default_team_id(client: ADOClient, project: str) -> str:
    try:
        url = client._dev_url(f"_apis/projects/{project}")
        proj = client.get(url)
        return proj.get("defaultTeam", {}).get("id", "")
    except ADOError:
        return ""


def _strip_dashboard_server_fields(dash: dict) -> dict:
    skip = {"id", "url", "_links", "eTag", "lastAccessedDate", "ownerId"}
    body = {k: v for k, v in dash.items() if k not in skip}
    # Strip widget IDs so they get regenerated
    widgets = body.get("widgets", [])
    clean_widgets = []
    for w in widgets:
        cw = {k: v for k, v in w.items() if k not in ("id", "eTag", "url", "_links")}
        clean_widgets.append(cw)
    body["widgets"] = clean_widgets
    return body
