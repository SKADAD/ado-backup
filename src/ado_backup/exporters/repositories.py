"""Repository exporter: git mirror clones, PRs, policies, permissions."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from ado_backup.client import ADOClient, ADOError
from ado_backup.checkpoint import Checkpoint, make_key

log = logging.getLogger(__name__)


def export_repositories(
    client: ADOClient,
    project: str,
    project_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    auth_headers: dict,
    concurrency: int = 4,
) -> None:
    repos_dir = project_dir / "repositories"
    repos_dir.mkdir(parents=True, exist_ok=True)

    try:
        repos = client.list_repos(project)
    except ADOError as exc:
        log.error("Could not list repos for project %s: %s", project, exc)
        manifest.record_item(project, "repositories", status="error", reason=str(exc))
        return

    with ThreadPoolExecutor(max_workers=min(concurrency, len(repos) or 1)) as pool:
        futures = {
            pool.submit(_export_one_repo, client, project, repo, repos_dir, manifest, checkpoint, auth_headers): repo
            for repo in repos
        }
        for fut in as_completed(futures):
            repo = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                log.error("Unhandled error backing up repo %s: %s", repo.get("name"), exc)


def _export_one_repo(
    client: ADOClient,
    project: str,
    repo: dict,
    repos_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    auth_headers: dict,
) -> None:
    repo_name = _safe_name(repo.get("name", repo["id"]))
    repo_id = repo["id"]
    repo_dir = repos_dir / repo_name
    repo_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()

    # ---- Metadata ----
    meta_key = make_key(project, "repo-meta", repo_id)
    if not checkpoint.is_done(meta_key):
        _write_json(repo_dir / "repo.json", repo)
        checkpoint.mark_done(meta_key)

    # ---- Mirror clone ----
    remote_url = repo.get("remoteUrl", "")
    clone_key = make_key(project, "repo-clone", repo_id)
    git_dir = repo_dir / "repo.git"

    if repo.get("isDisabled") or not remote_url:
        reason = "disabled" if repo.get("isDisabled") else "no remote URL"
        manifest.record_item(
            project, "repositories", name=repo_name, status="skipped", reason=reason,
            duration=time.monotonic() - t0,
        )
        return

    if not checkpoint.is_done(clone_key):
        # Inject credentials into URL for git
        auth_url = _inject_auth(remote_url, auth_headers)
        success, error = _git_mirror_clone(auth_url, git_dir)
        if not success:
            log.error("git clone --mirror failed for %s: %s", repo_name, error)
            manifest.record_item(
                project, "repositories", name=repo_name, status="error", reason=error,
                duration=time.monotonic() - t0,
            )
            return
        checkpoint.mark_done(clone_key)

    # ---- Pull requests ----
    pr_key = make_key(project, "repo-prs", repo_id)
    if not checkpoint.is_done(pr_key):
        _export_pull_requests(client, project, repo_id, repo_dir)
        checkpoint.mark_done(pr_key)

    # ---- Branch policies ----
    policy_key = make_key(project, "repo-policies", repo_id)
    if not checkpoint.is_done(policy_key):
        _export_policies(client, project, repo_id, repo_dir)
        checkpoint.mark_done(policy_key)

    # ---- Permissions ----
    perm_key = make_key(project, "repo-perms", repo_id)
    if not checkpoint.is_done(perm_key):
        _export_repo_permissions(client, project, repo_id, repo_dir)
        checkpoint.mark_done(perm_key)

    # Calculate approximate bytes
    bytes_written = _dir_size(repo_dir)
    manifest.record_item(
        project, "repositories", name=repo_name, status="ok",
        bytes_written=bytes_written, duration=time.monotonic() - t0,
    )


def _export_pull_requests(client: ADOClient, project: str, repo_id: str, repo_dir: Path) -> None:
    pr_dir = repo_dir / "pull-requests"
    pr_dir.mkdir(exist_ok=True)

    try:
        url = client._dev_url(f"{project}/_apis/git/repositories/{repo_id}/pullrequests")
        # Fetch all statuses
        prs_active = client.get(url, {"searchCriteria.status": "all", "$top": "1000"})
        prs = prs_active.get("value", [])
    except ADOError as exc:
        log.warning("Could not fetch PRs for repo %s: %s", repo_id, exc)
        return

    index = []
    for pr in prs:
        pr_id = pr["pullRequestId"]
        index.append({"id": pr_id, "title": pr.get("title"), "status": pr.get("status")})
        detail_path = pr_dir / f"pr-{pr_id}.json"
        if detail_path.exists():
            continue
        try:
            # Full PR detail with threads + work items
            detail_url = client._dev_url(
                f"{project}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}"
            )
            detail = client.get(detail_url, {"$expand": "all"})

            threads_url = client._dev_url(
                f"{project}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/threads"
            )
            threads = client.get(threads_url).get("value", [])
            detail["_threads"] = threads

            wi_url = client._dev_url(
                f"{project}/_apis/git/repositories/{repo_id}/pullrequests/{pr_id}/workitems"
            )
            try:
                wis = client.get(wi_url).get("value", [])
                detail["_linkedWorkItems"] = wis
            except ADOError:
                pass

            _write_json(detail_path, detail)
        except ADOError as exc:
            log.warning("Could not fetch PR %s detail: %s", pr_id, exc)

    _write_json(pr_dir / "index.json", index)


def _export_policies(client: ADOClient, project: str, repo_id: str, repo_dir: Path) -> None:
    try:
        url = client._dev_url(f"{project}/_apis/policy/configurations")
        data = client.get(url)
        configs = data.get("value", [])
        # Filter to this repo's policies
        repo_policies = [
            c for c in configs
            if any(
                s.get("repositoryId") == repo_id
                for scope in [c.get("settings", {}).get("scope", [])]
                for s in scope
            )
        ]
        _write_json(repo_dir / "policies.json", repo_policies)
    except ADOError as exc:
        log.warning("Could not fetch policies for repo %s: %s", repo_id, exc)


def _export_repo_permissions(client: ADOClient, project: str, repo_id: str, repo_dir: Path) -> None:
    try:
        # Namespace for Git repos
        namespace_id = "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87"
        token = f"repoV2/{project}/{repo_id}"
        url = client._dev_url(f"_apis/accesscontrollists/{namespace_id}")
        data = client.get(url, {"token": token, "includeExtendedInfo": "true"})
        _write_json(repo_dir / "permissions.json", data)
    except ADOError as exc:
        log.warning("Could not fetch permissions for repo %s: %s", repo_id, exc)


def _git_mirror_clone(remote_url: str, dest: Path) -> tuple[bool, str]:
    """Run git clone --mirror, or git remote update if dest already exists."""
    if dest.exists():
        # Resume: just fetch any new refs
        result = subprocess.run(
            ["git", "--git-dir", str(dest), "remote", "update", "--prune"],
            capture_output=True, text=True, timeout=600,
        )
    else:
        result = subprocess.run(
            ["git", "clone", "--mirror", remote_url, str(dest)],
            capture_output=True, text=True, timeout=3600,
        )

    if result.returncode != 0:
        # Sanitize URL from error output to avoid leaking credentials
        err = result.stderr.replace(remote_url, "<remote-url>")
        return False, err.strip()
    return True, ""


def _inject_auth(url: str, headers: dict) -> str:
    """Inject Basic auth credentials into a git HTTPS URL."""
    import re, base64
    auth = headers.get("Authorization", "")
    if auth.startswith("Basic "):
        encoded = auth[6:]
        credentials = base64.b64decode(encoded).decode()
        # credentials is ":PAT" for PAT auth
        if url.startswith("https://"):
            return url.replace("https://", f"https://{credentials}@", 1)
    elif auth.startswith("Bearer "):
        token = auth[7:]
        if url.startswith("https://"):
            return url.replace("https://", f"https://oauth:{token}@", 1)
    return url


def _safe_name(name: str) -> str:
    """Sanitize a name for use as a filesystem path component."""
    name = name.strip()
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    # Collapse any remaining `..` sequences that could allow traversal
    while ".." in name:
        name = name.replace("..", "_")
    name = name.strip(".")
    return name[:200] or "unnamed"


def _write_json(path: Path, data) -> int:
    text = json.dumps(data, indent=2, default=str)
    path.write_text(text)
    return len(text.encode())


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
