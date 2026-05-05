"""Wikis exporter: mirror clone + page tree JSON."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError
from ado_backup.checkpoint import Checkpoint, make_key
from ado_backup.exporters.repositories import _inject_auth, _safe_name

log = logging.getLogger(__name__)


def export_wikis(
    client: ADOClient,
    project: str,
    project_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    auth_headers: dict,
) -> None:
    wikis_dir = project_dir / "wikis"
    wikis_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/wiki/wikis")
        data = client.get(url)
        wikis = data.get("value", [])
    except ADOError as exc:
        log.warning("Could not list wikis for %s: %s", project, exc)
        manifest.record_item(project, "wikis", status="error", reason=str(exc))
        return

    for wiki in wikis:
        _export_one_wiki(client, project, wiki, wikis_dir, manifest, checkpoint, auth_headers)


def _export_one_wiki(
    client: ADOClient,
    project: str,
    wiki: dict,
    wikis_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    auth_headers: dict,
) -> None:
    wiki_name = _safe_name(wiki.get("name") or wiki.get("id", "wiki"))
    wiki_id = wiki.get("id", wiki_name)
    wiki_dir = wikis_dir / wiki_name
    wiki_dir.mkdir(exist_ok=True)

    t0 = time.monotonic()

    # Metadata
    meta_key = make_key(project, "wiki-meta", wiki_id)
    if not checkpoint.is_done(meta_key):
        _write_json(wiki_dir / "wiki.json", wiki)
        checkpoint.mark_done(meta_key)

    # Git mirror clone
    remote_url = wiki.get("remoteUrl", "")
    clone_key = make_key(project, "wiki-clone", wiki_id)
    git_dir = wiki_dir / "wiki.git"

    if remote_url and not checkpoint.is_done(clone_key):
        auth_url = _inject_auth(remote_url, auth_headers)
        if git_dir.exists():
            result = subprocess.run(
                ["git", "--git-dir", str(git_dir), "remote", "update", "--prune"],
                capture_output=True, text=True, timeout=600,
            )
        else:
            result = subprocess.run(
                ["git", "clone", "--mirror", auth_url, str(git_dir)],
                capture_output=True, text=True, timeout=3600,
            )
        if result.returncode == 0:
            checkpoint.mark_done(clone_key)
        else:
            err = result.stderr.replace(auth_url, "<remote-url>")
            log.warning("Wiki git clone failed for %s: %s", wiki_name, err.strip())

    # Page tree as JSON
    pages_key = make_key(project, "wiki-pages", wiki_id)
    if not checkpoint.is_done(pages_key):
        _export_wiki_pages(client, project, wiki_id, wiki_dir)
        checkpoint.mark_done(pages_key)

    manifest.record_item(
        project, "wikis", name=wiki_name, status="ok",
        duration=time.monotonic() - t0,
    )


def _export_wiki_pages(client: ADOClient, project: str, wiki_id: str, wiki_dir: Path) -> None:
    try:
        url = client._dev_url(f"{project}/_apis/wiki/wikis/{wiki_id}/pages")
        data = client.get(url, {"recursionLevel": "full", "includeContent": "false"})
        _write_json(wiki_dir / "pages.json", data)
    except ADOError as exc:
        log.warning("Could not fetch wiki pages for %s: %s", wiki_id, exc)


def _write_json(path: Path, data) -> int:
    text = json.dumps(data, indent=2, default=str)
    path.write_text(text)
    return len(text.encode())
