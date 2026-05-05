"""CLI entry point for ado-backup."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from ado_backup import __version__
from ado_backup.config import BackupConfig
from ado_backup.manifest import Manifest
from ado_backup.zipper import zip_directory, verify_archive

app = typer.Typer(
    name="ado-backup",
    help="Comprehensive backup tool for Azure DevOps organizations.",
    no_args_is_help=True,
)


def _setup_logging(log_format: str, log_level: str, log_file: Optional[Path] = None):
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = []

    if log_format == "json":
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    handlers.append(stderr_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)

    # Suppress noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d)


@app.command(name="backup")
def backup_cmd(
    org: str = typer.Option(..., "--org", help="ADO organization name or URL"),
    pat: Optional[str] = typer.Option(None, "--pat", help="Personal Access Token (or set AZURE_DEVOPS_PAT)"),
    use_az_cli: bool = typer.Option(False, "--use-az-cli", help="Authenticate via Azure CLI"),
    service_principal: bool = typer.Option(False, "--service-principal", help="Authenticate via service principal (AZURE_CLIENT_ID/TENANT_ID/CLIENT_SECRET)"),
    projects: Optional[str] = typer.Option(None, "--projects", help="Comma-separated project names to back up (default: all)"),
    output_dir: str = typer.Option("./backups", "--output-dir", help="Directory for output archives"),
    concurrency: int = typer.Option(4, "--concurrency", help="Number of parallel project workers"),
    include_logs: bool = typer.Option(False, "--include-logs", help="Download pipeline run logs (large!)"),
    include_package_binaries: bool = typer.Option(False, "--include-package-binaries", help="Download artifact package binaries (large!)"),
    runs_per_pipeline: int = typer.Option(200, "--runs-per-pipeline", help="Max pipeline run history records"),
    runs_per_test_plan: int = typer.Option(100, "--runs-per-test-plan", help="Max test run history records"),
    no_zip: bool = typer.Option(False, "--no-zip", help="Skip zipping (useful for debugging)"),
    zip64: bool = typer.Option(False, "--zip-format-zip64", help="Force ZIP64 format"),
    force: bool = typer.Option(False, "--force", help="Re-do all work even if already checkpointed"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be backed up without doing it"),
    log_format: str = typer.Option("text", "--log-format", help="Log format: text or json"),
    log_level: str = typer.Option("INFO", "--log-level", help="Log level: DEBUG, INFO, WARNING, ERROR"),
):
    """Back up an entire Azure DevOps organization to a timestamped ZIP archive."""
    from ado_backup.auth import AuthError
    from ado_backup.runner import run_backup

    project_list = [p.strip() for p in projects.split(",") if p.strip()] if projects else []

    config = BackupConfig.from_env_and_args(
        org=org,
        pat=pat,
        use_az_cli=use_az_cli,
        use_service_principal=service_principal,
        projects=project_list,
        output_dir=output_dir,
        no_zip=no_zip,
        zip_format_zip64=zip64,
        force=force,
        dry_run=dry_run,
        concurrency=concurrency,
        include_logs=include_logs,
        include_package_binaries=include_package_binaries,
        runs_per_pipeline=runs_per_pipeline,
        runs_per_test_plan=runs_per_test_plan,
        log_format=log_format,
        log_level=log_level,
    )

    # Build run dir name
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_name = f"ado-backup-{config.org}-{ts}"
    output_path = Path(config.output_dir)
    run_dir = output_path / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "projects").mkdir(exist_ok=True)

    log_file = run_dir / "run.log"
    _setup_logging(log_format, log_level, log_file)
    log = logging.getLogger("ado_backup.cli")

    log.info("ado-backup %s starting — org=%s", __version__, config.org)

    # Validate auth early
    try:
        from ado_backup.auth import build_request_headers
        build_request_headers(config)
    except AuthError as exc:
        log.error("Authentication error: %s", exc)
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1)

    manifest = Manifest(config.org, config, run_dir)

    t0 = time.monotonic()
    exit_code = run_backup(config, run_dir, manifest)
    elapsed = time.monotonic() - t0

    if dry_run:
        raise typer.Exit(exit_code)

    # Create ZIP archive
    if not no_zip:
        zip_path = output_path / f"{run_name}.zip"
        try:
            zip_directory(run_dir, zip_path, force_zip64=zip64)
            log.info("Archive: %s", zip_path)
            typer.echo(f"Archive: {zip_path}")
        except OSError as exc:
            log.error("Failed to create archive: %s", exc)

    summary = manifest.summary()
    typer.echo(
        f"Done in {elapsed:.1f}s — "
        f"projects={summary['total_projects']}, "
        f"repos={summary['total_repositories']}, "
        f"work_items={summary['total_work_items']}, "
        f"errors={summary['errors']}, "
        f"warnings={summary['warnings']}"
    )

    raise typer.Exit(exit_code)


@app.command(name="verify")
def verify_cmd(
    archive: Path = typer.Argument(..., help="Path to the ZIP archive to verify"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Verify a backup archive's integrity and manifest consistency."""
    import zipfile

    if not archive.exists():
        typer.echo(f"ERROR: Archive not found: {archive}", err=True)
        raise typer.Exit(1)

    # Basic ZIP integrity
    ok, errors = verify_archive(archive)
    if not ok:
        for err in errors:
            typer.echo(f"ERROR: {err}", err=True)
        raise typer.Exit(1)

    # Manifest check
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            names = set(zf.namelist())

            # Find manifest.json (it's inside the run dir)
            manifest_entries = [n for n in names if n.endswith("/manifest.json") or n == "manifest.json"]
            if not manifest_entries:
                typer.echo("ERROR: manifest.json not found in archive", err=True)
                raise typer.Exit(1)

            manifest_entry = manifest_entries[0]
            manifest = json.loads(zf.read(manifest_entry))

            schema = manifest.get("schema_version")
            org = manifest.get("organization")
            summary = manifest.get("summary", {})
            errors_count = summary.get("errors", -1)

            typer.echo(f"Archive: {archive}")
            typer.echo(f"Organization: {org}")
            typer.echo(f"Schema version: {schema}")
            typer.echo(f"Tool version: {manifest.get('tool_version')}")
            typer.echo(f"Started: {manifest.get('started_at')}")
            typer.echo(f"Duration: {manifest.get('duration_seconds')}s")
            typer.echo(f"Projects: {summary.get('total_projects')}")
            typer.echo(f"Repositories: {summary.get('total_repositories')}")
            typer.echo(f"Work items: {summary.get('total_work_items')}")
            typer.echo(f"Errors: {errors_count}")
            typer.echo(f"Warnings: {summary.get('warnings')}")

            if verbose:
                for proj in manifest.get("projects", []):
                    typer.echo(f"\nProject: {proj['name']}")
                    for item in proj.get("items", []):
                        status = item["status"]
                        icon = "✓" if status == "ok" else ("⚠" if status == "skipped" else "✗")
                        typer.echo(f"  {icon} {item['category']}: {status}")

            if errors_count != 0:
                typer.echo(f"\nWARNING: manifest reports {errors_count} error(s)")
                raise typer.Exit(1)

    except (KeyError, json.JSONDecodeError) as exc:
        typer.echo(f"ERROR: Could not parse manifest: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo("\nVerification PASSED")
    raise typer.Exit(0)


# Make `ado-backup` (no subcommand) run the backup command
@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
