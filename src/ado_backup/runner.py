"""Orchestration: enumerate projects, dispatch exporters, manage progress."""

from __future__ import annotations

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ado_backup.auth import build_request_headers
from ado_backup.checkpoint import Checkpoint
from ado_backup.client import ADOClient, ADOError
from ado_backup.config import BackupConfig
from ado_backup.manifest import Manifest
from ado_backup.exporters.organization import export_organization
from ado_backup.exporters.repositories import export_repositories
from ado_backup.exporters.work_items import export_work_items
from ado_backup.exporters.pipelines import export_pipelines
from ado_backup.exporters.wikis import export_wikis
from ado_backup.exporters.boards import export_boards
from ado_backup.exporters.artifacts import export_artifacts
from ado_backup.exporters.test_plans import export_test_plans

log = logging.getLogger(__name__)


def run_backup(config: BackupConfig, run_dir: Path, manifest: Manifest) -> int:
    """Main backup orchestration. Returns exit code."""
    checkpoint = Checkpoint(run_dir, force=config.force)
    auth_headers = build_request_headers(config)

    with ADOClient(config) as client:
        # ---- Dry run ----
        if config.dry_run:
            return _dry_run(client, config)

        # ---- Discover projects ----
        try:
            all_projects = client.list_projects()
        except ADOError as exc:
            log.error("Failed to list projects: %s", exc)
            return 1

        if config.projects:
            project_filter = {p.lower() for p in config.projects}
            projects = [p for p in all_projects if p["name"].lower() in project_filter]
            not_found = project_filter - {p["name"].lower() for p in projects}
            for nf in not_found:
                log.warning("Project not found in org: %s", nf)
        else:
            projects = all_projects

        log.info("Backing up %d projects from org '%s'", len(projects), config.org)

        # ---- Org-level export ----
        org_dir = run_dir / "organization"
        export_organization(client, org_dir, manifest)

        # ---- Per-project export ----
        _run_projects(client, config, projects, run_dir, manifest, checkpoint, auth_headers)

    manifest.finalize()
    summary = manifest.summary()

    log.info(
        "Backup complete. Projects: %d, Repos: %d, Work items: %d, Errors: %d, Warnings: %d",
        summary["total_projects"],
        summary["total_repositories"],
        summary["total_work_items"],
        summary["errors"],
        summary["warnings"],
    )

    return 0 if summary["errors"] == 0 else 1


def _run_projects(
    client: ADOClient,
    config: BackupConfig,
    projects: list[dict],
    run_dir: Path,
    manifest: Manifest,
    checkpoint: Checkpoint,
    auth_headers: dict,
) -> None:
    _use_progress = sys.stderr.isatty()

    if _use_progress:
        _run_with_progress(client, config, projects, run_dir, manifest, checkpoint, auth_headers)
    else:
        _run_plain(client, config, projects, run_dir, manifest, checkpoint, auth_headers)


def _run_with_progress(client, config, projects, run_dir, manifest, checkpoint, auth_headers):
    try:
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            transient=False,
        ) as progress:
            overall = progress.add_task("Projects", total=len(projects))
            with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
                futures = {
                    pool.submit(
                        _backup_project, client, config, p, run_dir, manifest, checkpoint, auth_headers
                    ): p
                    for p in projects
                }
                for fut in as_completed(futures):
                    proj = futures[fut]
                    try:
                        fut.result()
                    except Exception as exc:
                        log.error("Unhandled error in project %s: %s", proj["name"], exc)
                    progress.advance(overall)
    except ImportError:
        _run_plain(client, config, projects, run_dir, manifest, checkpoint, auth_headers)


def _run_plain(client, config, projects, run_dir, manifest, checkpoint, auth_headers):
    with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        futures = {
            pool.submit(
                _backup_project, client, config, p, run_dir, manifest, checkpoint, auth_headers
            ): p
            for p in projects
        }
        for fut in as_completed(futures):
            proj = futures[fut]
            try:
                fut.result()
                log.info("Completed project: %s", proj["name"])
            except Exception as exc:
                log.error("Unhandled error in project %s: %s", proj["name"], exc)


def _backup_project(
    client: ADOClient,
    config: BackupConfig,
    project: dict,
    run_dir: Path,
    manifest: Manifest,
    checkpoint: Checkpoint,
    auth_headers: dict,
) -> None:
    name = project["name"]
    project_id = project["id"]

    manifest.add_project(name, project_id)

    project_dir = run_dir / "projects" / _safe_name(name)
    project_dir.mkdir(parents=True, exist_ok=True)

    # Project metadata
    import json
    _write_json(project_dir / "project.json", project)

    log.info("Backing up project: %s", name)

    # Repositories
    export_repositories(
        client, name, project_dir, manifest, checkpoint, auth_headers, config.concurrency
    )

    # Work items
    export_work_items(client, name, project_dir, manifest, checkpoint, config.concurrency)

    # Pipelines
    export_pipelines(
        client, name, project_dir, manifest, checkpoint,
        runs_per_pipeline=config.runs_per_pipeline,
        include_logs=config.include_logs,
    )

    # Wikis
    export_wikis(client, name, project_dir, manifest, checkpoint, auth_headers)

    # Boards + dashboards + process
    export_boards(client, name, project_dir, manifest, checkpoint)

    # Artifacts
    export_artifacts(
        client, name, project_dir, manifest, checkpoint,
        include_binaries=config.include_package_binaries,
    )

    # Test plans
    export_test_plans(
        client, name, project_dir, manifest, checkpoint,
        runs_per_test_plan=config.runs_per_test_plan,
    )


def _dry_run(client: ADOClient, config: BackupConfig) -> int:
    print(f"Dry run for org: {config.org}")
    try:
        projects = client.list_projects()
    except ADOError as exc:
        print(f"ERROR: Could not list projects: {exc}")
        return 1

    if config.projects:
        filter_set = {p.lower() for p in config.projects}
        projects = [p for p in projects if p["name"].lower() in filter_set]

    print(f"Would back up {len(projects)} project(s):")
    for p in projects:
        print(f"  - {p['name']} ({p['id']})")
    print("\nCategories per project:")
    for cat in [
        "repositories", "work-items", "pipelines", "wikis",
        "boards/dashboards", "artifacts", "test-plans",
    ]:
        print(f"  - {cat}")
    return 0


def _safe_name(name: str) -> str:
    name = name.strip()
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    while ".." in name:
        name = name.replace("..", "_")
    name = name.strip(".")
    return name[:200] or "unnamed"


def _write_json(path: Path, data) -> None:
    import json
    path.write_text(json.dumps(data, indent=2, default=str))
