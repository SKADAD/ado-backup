"""Repository restorer: create ADO repos and push mirror clones."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from ado_backup.client import ADOClient, ADOError
from ado_backup.auth import build_request_headers

log = logging.getLogger(__name__)


def restore_repositories(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    report,
    force: bool = False,
    concurrency: int = 4,
    dry_run: bool = False,
) -> None:
    repos_dir = project_backup_dir / "repositories"
    if not repos_dir.exists():
        report.record(source_project, "repositories", status="skipped", reason="No repositories directory in backup")
        return

    repo_dirs = [d for d in repos_dir.iterdir() if d.is_dir()]
    if not repo_dirs:
        report.record(source_project, "repositories", status="ok", count=0)
        return

    # Fetch existing repos in target project once
    try:
        existing_repos = {r["name"].lower(): r for r in client.list_repos(target_project)}
    except ADOError as exc:
        log.warning("Could not list existing repos in %s: %s", target_project, exc)
        existing_repos = {}

    auth_headers = build_request_headers(client._config)

    with ThreadPoolExecutor(max_workers=min(concurrency, len(repo_dirs))) as pool:
        futures = {
            pool.submit(
                _restore_one_repo,
                client, source_project, target_project, repo_dir,
                existing_repos, auth_headers, report, force, dry_run,
            ): repo_dir.name
            for repo_dir in repo_dirs
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                log.error("Unhandled error restoring repo %s: %s", name, exc)
                report.record(source_project, "repositories", name=name, status="error", reason=str(exc))


def _restore_one_repo(
    client: ADOClient,
    source_project: str,
    target_project: str,
    repo_dir: Path,
    existing_repos: dict,
    auth_headers: dict,
    report,
    force: bool,
    dry_run: bool,
) -> None:
    repo_name = repo_dir.name
    git_dir = repo_dir / "repo.git"
    meta_path = repo_dir / "repo.json"

    t0 = time.monotonic()

    if not git_dir.exists():
        report.record(
            source_project, "repositories", name=repo_name, status="skipped",
            reason="No bare clone (repo.git) found in backup",
        )
        return

    if dry_run:
        action = "create" if repo_name.lower() not in existing_repos else ("push (force)" if force else "skip (exists)")
        log.info("[dry-run] Would %s repo %s/%s", action, target_project, repo_name)
        report.record(source_project, "repositories", name=repo_name, status="ok", reason="dry-run")
        return

    # Read backup metadata to get default branch etc.
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            pass

    # Create the repo in the target project if it doesn't exist
    if repo_name.lower() not in existing_repos:
        try:
            created = _create_repo(client, target_project, repo_name, meta)
            remote_url = created.get("remoteUrl", "")
            log.info("Created repo %s/%s", target_project, repo_name)
        except ADOError as exc:
            report.record(
                source_project, "repositories", name=repo_name, status="error",
                reason=f"Could not create repo: {exc}",
            )
            return
    else:
        existing = existing_repos[repo_name.lower()]
        if not force:
            report.record(
                source_project, "repositories", name=repo_name, status="skipped",
                reason="Repo already exists; use --force to overwrite",
            )
            return
        remote_url = existing.get("remoteUrl", "")
        log.info("Repo %s/%s already exists; pushing (--force)", target_project, repo_name)

    if not remote_url:
        report.record(
            source_project, "repositories", name=repo_name, status="error",
            reason="No remote URL for target repo",
        )
        return

    # Push the mirror
    auth_url = _inject_auth(remote_url, auth_headers)
    ok, err = _git_push_mirror(git_dir, auth_url)
    if not ok:
        report.record(
            source_project, "repositories", name=repo_name, status="error",
            reason=f"git push --mirror failed: {err}",
        )
        return

    report.record(
        source_project, "repositories", name=repo_name, status="ok",
        duration=time.monotonic() - t0,
    )


def _create_repo(client: ADOClient, project: str, name: str, meta: dict) -> dict:
    url = client._dev_url(f"{project}/_apis/git/repositories")
    body = {"name": name}
    # Set default branch if available
    if meta.get("defaultBranch"):
        body["defaultBranch"] = meta["defaultBranch"]
    return client.post(url, body)


def _git_push_mirror(git_dir: Path, remote_url: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "--git-dir", str(git_dir), "push", "--mirror", remote_url],
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0:
        err = result.stderr.replace(remote_url, "<remote-url>")
        return False, err.strip()
    return True, ""


def _inject_auth(url: str, headers: dict) -> str:
    import base64
    auth = headers.get("Authorization", "")
    if auth.startswith("Basic "):
        credentials = base64.b64decode(auth[6:]).decode()
        if url.startswith("https://"):
            return url.replace("https://", f"https://{credentials}@", 1)
    elif auth.startswith("Bearer "):
        token = auth[7:]
        if url.startswith("https://"):
            return url.replace("https://", f"https://oauth:{token}@", 1)
    return url
