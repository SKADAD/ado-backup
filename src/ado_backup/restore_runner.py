"""Restore orchestration: extract archive, create/find projects, run per-project restorers."""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from ado_backup.client import ADOClient, ADOError
from ado_backup.config import RestoreConfig, ALL_CATEGORIES
from ado_backup.restore_report import RestoreReport
from ado_backup.restorers.repositories import restore_repositories
from ado_backup.restorers.work_items import restore_work_items
from ado_backup.restorers.pipelines import restore_pipelines
from ado_backup.restorers.wikis import restore_wikis
from ado_backup.restorers.boards import restore_boards
from ado_backup.restorers.artifacts import restore_artifacts
from ado_backup.restorers.test_plans import restore_test_plans

log = logging.getLogger(__name__)


def run_restore(config: RestoreConfig, report: RestoreReport) -> int:
    """Main restore orchestration. Returns exit code (0 = success)."""
    archive_path = Path(config.archive)
    if not archive_path.exists():
        log.error("Archive not found: %s", archive_path)
        return 1

    # Determine effective categories
    categories = set(config.categories) if config.categories else set(ALL_CATEGORIES)

    # Extract archive if needed, or use directory directly
    if archive_path.is_dir():
        run_dir = archive_path
        _cleanup = False
    else:
        tmp_dir = tempfile.mkdtemp(prefix="ado-restore-")
        log.info("Extracting archive to %s", tmp_dir)
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(tmp_dir)
        except zipfile.BadZipFile as exc:
            log.error("Bad ZIP file: %s", exc)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return 1
        # The archive contains a single top-level directory
        extracted = list(Path(tmp_dir).iterdir())
        run_dir = extracted[0] if len(extracted) == 1 and extracted[0].is_dir() else Path(tmp_dir)
        _cleanup = True

    try:
        return _do_restore(config, report, run_dir, categories)
    finally:
        if _cleanup:
            shutil.rmtree(Path(run_dir).parent if run_dir.parent != Path(tmp_dir) else run_dir,
                          ignore_errors=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _do_restore(
    config: RestoreConfig,
    report: RestoreReport,
    run_dir: Path,
    categories: set[str],
) -> int:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        log.error("No manifest.json found in archive at %s", run_dir)
        return 1

    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Could not read manifest: %s", exc)
        return 1

    source_org = manifest.get("organization", "")
    log.info("Restoring backup of org '%s' → target org '%s'", source_org, config.org)

    # Determine which projects to restore
    manifest_projects = {p["name"]: p for p in manifest.get("projects", [])}
    if config.projects:
        filter_set = {p.lower() for p in config.projects}
        selected = {name: data for name, data in manifest_projects.items() if name.lower() in filter_set}
        not_found = filter_set - {n.lower() for n in selected}
        for nf in not_found:
            log.warning("Project '%s' not found in archive", nf)
    else:
        selected = manifest_projects

    if not selected:
        log.error("No projects to restore (check --projects filter)")
        return 1

    log.info("Restoring %d project(s): %s", len(selected), ", ".join(selected))

    if config.dry_run:
        _do_dry_run(config, selected, run_dir, categories)
        return 0

    with ADOClient(config) as client:
        # Ensure target projects exist
        existing_projects = _get_existing_projects(client)

        _run_projects(
            client, config, selected, existing_projects, run_dir, categories, report
        )

    summary = report.finalize()
    log.info(
        "Restore complete. Projects: %d, Errors: %d, Warnings: %d",
        summary["total_projects"],
        summary["errors"],
        summary["warnings"],
    )
    return 0 if summary["errors"] == 0 else 1


def _get_existing_projects(client: ADOClient) -> dict[str, dict]:
    try:
        return {p["name"].lower(): p for p in client.list_projects()}
    except ADOError as exc:
        log.warning("Could not list existing projects: %s", exc)
        return {}


def _run_projects(
    client: ADOClient,
    config: RestoreConfig,
    selected: dict[str, dict],
    existing_projects: dict[str, dict],
    run_dir: Path,
    categories: set[str],
    report: RestoreReport,
) -> None:
    use_progress = sys.stderr.isatty()

    def _work(source_name: str, project_data: dict):
        target_name = config.target_project_name(source_name)
        project_backup_dir = run_dir / "projects" / source_name
        if not project_backup_dir.exists():
            log.warning("No backup directory found for project %s", source_name)
            return

        # Ensure target project exists
        if target_name.lower() not in existing_projects:
            if config.create_projects:
                log.info("Creating project %s in %s", target_name, config.org)
                try:
                    client.create_project(target_name)
                    log.info("Project %s created", target_name)
                except ADOError as exc:
                    log.error("Could not create project %s: %s", target_name, exc)
                    report.add_project(source_name, target_name)
                    report.record(
                        source_name, "project-creation", status="error",
                        reason=str(exc),
                    )
                    return
            else:
                log.error("Project %s does not exist in target org and --no-create-projects is set", target_name)
                report.add_project(source_name, target_name)
                report.record(source_name, "project-creation", status="error",
                               reason="Project does not exist; run without --no-create-projects")
                return

        report.add_project(source_name, target_name)
        _restore_project(client, config, source_name, target_name, project_backup_dir, categories, report)

    if use_progress:
        try:
            from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(), TaskProgressColumn(), TimeElapsedColumn(),
            ) as progress:
                task = progress.add_task("Restoring projects", total=len(selected))
                with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
                    futures = {pool.submit(_work, name, data): name for name, data in selected.items()}
                    for fut in as_completed(futures):
                        name = futures[fut]
                        try:
                            fut.result()
                        except Exception as exc:
                            log.error("Unhandled error restoring project %s: %s", name, exc)
                        progress.advance(task)
            return
        except ImportError:
            pass

    with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        futures = {pool.submit(_work, name, data): name for name, data in selected.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                fut.result()
                log.info("Completed restore of project: %s", name)
            except Exception as exc:
                log.error("Unhandled error restoring project %s: %s", name, exc)


def _restore_project(
    client: ADOClient,
    config: RestoreConfig,
    source_name: str,
    target_name: str,
    project_backup_dir: Path,
    categories: set[str],
    report: RestoreReport,
) -> None:
    log.info("Restoring project %s → %s", source_name, target_name)

    wi_id_map: dict[int, int] = {}

    if "repositories" in categories:
        restore_repositories(
            client, source_name, target_name, project_backup_dir, report,
            force=config.force, concurrency=config.concurrency, dry_run=config.dry_run,
        )

    if "work-items" in categories:
        wi_id_map = restore_work_items(
            client, source_name, target_name, project_backup_dir, report,
            force=config.force, dry_run=config.dry_run,
        )

    if "pipelines" in categories:
        restore_pipelines(
            client, source_name, target_name, project_backup_dir, report,
            force=config.force, dry_run=config.dry_run,
        )

    if "wikis" in categories:
        restore_wikis(
            client, source_name, target_name, project_backup_dir, report,
            force=config.force, dry_run=config.dry_run,
        )

    if "boards" in categories:
        restore_boards(
            client, source_name, target_name, project_backup_dir, report,
            force=config.force, dry_run=config.dry_run,
        )

    if "artifacts" in categories:
        restore_artifacts(
            client, source_name, target_name, project_backup_dir, report,
            force=config.force, dry_run=config.dry_run,
        )

    if "test-plans" in categories:
        restore_test_plans(
            client, source_name, target_name, project_backup_dir, report,
            wi_id_map=wi_id_map, force=config.force, dry_run=config.dry_run,
        )


def _do_dry_run(config: RestoreConfig, selected: dict, run_dir: Path, categories: set[str]) -> None:
    print(f"\nDry run — target org: {config.org}")
    print(f"Archive: {config.archive}")
    print(f"Categories: {', '.join(sorted(categories))}")
    print(f"\nWould restore {len(selected)} project(s):\n")
    for source_name in selected:
        target_name = config.target_project_name(source_name)
        label = f"{source_name} → {target_name}" if target_name != source_name else source_name
        print(f"  Project: {label}")
        project_dir = run_dir / "projects" / source_name
        for cat in sorted(categories):
            _print_category_preview(cat, project_dir)
    print()


def _print_category_preview(category: str, project_dir: Path) -> None:
    dir_map = {
        "repositories": "repositories",
        "work-items": "work-items/items",
        "pipelines": "pipelines",
        "wikis": "wikis",
        "boards": "dashboards",
        "artifacts": "artifacts/feeds",
        "test-plans": "test-plans",
    }
    sub = dir_map.get(category, category)
    path = project_dir / sub
    if path.exists():
        if path.is_dir():
            count = sum(1 for _ in path.iterdir())
            print(f"    - {category}: {count} item(s)")
        else:
            print(f"    - {category}: present")
    else:
        print(f"    - {category}: (not in backup)")
