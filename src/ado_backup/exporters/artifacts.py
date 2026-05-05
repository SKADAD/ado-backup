"""Artifacts exporter: feeds, packages (binaries optional)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError
from ado_backup.checkpoint import Checkpoint, make_key

log = logging.getLogger(__name__)


def export_artifacts(
    client: ADOClient,
    project: str,
    project_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    include_binaries: bool = False,
) -> None:
    art_dir = project_dir / "artifacts" / "feeds"
    art_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    try:
        # Project-scoped feeds
        url = client._feeds_url(f"{client._org}/{project}", "_apis/packaging/feeds")
        data = client.get(url)
        feeds = data.get("value", [])
    except ADOError:
        # Try org-scoped
        try:
            url = client._feeds_url(client._org, "_apis/packaging/feeds")
            data = client.get(url)
            feeds = data.get("value", [])
            # Filter to project
            feeds = [f for f in feeds if f.get("project", {}).get("name") == project]
        except ADOError as exc:
            log.warning("Could not fetch feeds for %s: %s", project, exc)
            manifest.record_item(project, "artifacts-feeds", status="error", reason=str(exc))
            return

    for feed in feeds:
        _export_one_feed(client, project, feed, art_dir, manifest, checkpoint, include_binaries)

    if not feeds:
        manifest.record_item(project, "artifacts-feeds", status="ok", count=0, duration=time.monotonic() - t0)


def _export_one_feed(
    client: ADOClient,
    project: str,
    feed: dict,
    art_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    include_binaries: bool,
) -> None:
    feed_name = _safe_name(feed.get("name", feed.get("id", "feed")))
    feed_id = feed.get("id", feed_name)
    feed_dir = art_dir / feed_name
    feed_dir.mkdir(exist_ok=True)

    t0 = time.monotonic()
    key = make_key(project, "feed", feed_id)
    if checkpoint.is_done(key):
        return

    _write_json(feed_dir / "feed.json", feed)

    # Packages
    try:
        url = client._feeds_url(
            f"{client._org}/{project}",
            f"_apis/packaging/feeds/{feed_id}/packages"
        )
        data = client.get(url, {"$top": "1000", "includeAllVersions": "false"})
        packages = data.get("value", [])
        _write_json(feed_dir / "packages.json", packages)

        if include_binaries:
            _download_packages(client, project, feed_id, packages, feed_dir)

    except ADOError as exc:
        log.warning("Could not fetch packages for feed %s: %s", feed_name, exc)

    # Views
    try:
        views_url = client._feeds_url(
            f"{client._org}/{project}",
            f"_apis/packaging/feeds/{feed_id}/views"
        )
        views = client.get(views_url)
        _write_json(feed_dir / "views.json", views)
    except ADOError:
        pass

    # Upstream sources
    try:
        ups_url = client._feeds_url(
            f"{client._org}/{project}",
            f"_apis/packaging/feeds/{feed_id}/upstreamsources"
        )
        ups = client.get(ups_url)
        _write_json(feed_dir / "upstream-sources.json", ups)
    except ADOError:
        pass

    checkpoint.mark_done(key)
    manifest.record_item(
        project, "artifacts-feeds", name=feed_name, status="ok",
        duration=time.monotonic() - t0,
    )


def _download_packages(
    client: ADOClient, project: str, feed_id: str, packages: list, feed_dir: Path
) -> None:
    bins_dir = feed_dir / "binaries"
    bins_dir.mkdir(exist_ok=True)

    for pkg in packages:
        pkg_name = _safe_name(pkg.get("name", "package"))
        pkg_type = pkg.get("protocolType", "").lower()
        versions = pkg.get("versions", [])
        if not versions:
            continue
        latest = versions[0]
        version = latest.get("version", "unknown")
        dest = bins_dir / f"{pkg_name}-{version}"
        if dest.exists():
            continue

        try:
            dl_url = client._feeds_url(
                f"{client._org}/{project}",
                f"_apis/packaging/feeds/{feed_id}/packages/{pkg['id']}/versions/{latest['id']}/content"
            )
            data = client.get_raw(dl_url)
            dest.write_bytes(data)
        except ADOError as exc:
            log.debug("Could not download package %s@%s: %s", pkg_name, version, exc)


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
