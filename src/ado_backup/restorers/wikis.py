"""Wikis restorer: create wiki in target project and push the mirror clone."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError
from ado_backup.auth import build_request_headers

log = logging.getLogger(__name__)


def restore_wikis(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    report,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    wikis_dir = project_backup_dir / "wikis"
    if not wikis_dir.exists():
        report.record(source_project, "wikis", status="skipped", reason="No wikis directory in backup")
        return

    wiki_dirs = [d for d in wikis_dir.iterdir() if d.is_dir()]
    if not wiki_dirs:
        report.record(source_project, "wikis", status="ok", count=0)
        return

    auth_headers = build_request_headers(client._config)

    for wiki_dir in wiki_dirs:
        _restore_one_wiki(client, source_project, target_project, wiki_dir, auth_headers, report, force, dry_run)


def _restore_one_wiki(
    client: ADOClient,
    source_project: str,
    target_project: str,
    wiki_dir: Path,
    auth_headers: dict,
    report,
    force: bool,
    dry_run: bool,
) -> None:
    wiki_name = wiki_dir.name
    git_dir = wiki_dir / "wiki.git"
    meta_path = wiki_dir / "wiki.json"

    t0 = time.monotonic()

    if not git_dir.exists():
        report.record(
            source_project, "wikis", name=wiki_name, status="skipped",
            reason="No bare clone (wiki.git) found in backup",
        )
        return

    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            pass

    wiki_type = meta.get("type", "projectWiki")

    if dry_run:
        log.info("[dry-run] Would restore wiki %s/%s", target_project, wiki_name)
        report.record(source_project, "wikis", name=wiki_name, status="ok", reason="dry-run")
        return

    # Fetch existing wikis
    remote_url = ""
    try:
        url = client._dev_url(f"{target_project}/_apis/wiki/wikis")
        existing = {w["name"]: w for w in client.get(url).get("value", [])}
    except ADOError:
        existing = {}

    if wiki_name in existing:
        if not force:
            report.record(
                source_project, "wikis", name=wiki_name, status="skipped",
                reason="Wiki already exists; use --force to overwrite",
            )
            return
        remote_url = existing[wiki_name].get("remoteUrl", "")
    else:
        # Create the wiki
        try:
            remote_url = _create_wiki(client, target_project, wiki_name, wiki_type, meta)
        except ADOError as exc:
            report.record(
                source_project, "wikis", name=wiki_name, status="error",
                reason=f"Could not create wiki: {exc}",
            )
            return

    if not remote_url:
        report.record(source_project, "wikis", name=wiki_name, status="error", reason="No remote URL")
        return

    auth_url = _inject_auth(remote_url, auth_headers)
    result = subprocess.run(
        ["git", "--git-dir", str(git_dir), "push", "--mirror", auth_url],
        capture_output=True, text=True, timeout=1800,
    )
    if result.returncode != 0:
        err = result.stderr.replace(auth_url, "<remote-url>")
        report.record(
            source_project, "wikis", name=wiki_name, status="error",
            reason=f"git push --mirror failed: {err.strip()}",
        )
        return

    report.record(source_project, "wikis", name=wiki_name, status="ok", duration=time.monotonic() - t0)


def _create_wiki(client: ADOClient, project: str, name: str, wiki_type: str, meta: dict) -> str:
    url = client._dev_url(f"{project}/_apis/wiki/wikis")
    body: dict = {"name": name, "type": wiki_type, "projectId": meta.get("projectId", "")}
    if wiki_type == "codeWiki":
        body["repositoryId"] = meta.get("repositoryId", "")
        body["mappedPath"] = meta.get("mappedPath", "/")
    result = client.post(url, body)
    return result.get("remoteUrl", "")


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
