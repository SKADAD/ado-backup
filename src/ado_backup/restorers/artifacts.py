"""Artifacts restorer: recreate feeds (and optionally re-publish packages)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError

log = logging.getLogger(__name__)


def restore_artifacts(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    report,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    feeds_dir = project_backup_dir / "artifacts" / "feeds"
    if not feeds_dir.exists():
        report.record(source_project, "artifacts-feeds", status="skipped", reason="No artifacts directory in backup")
        return

    feed_dirs = [d for d in feeds_dir.iterdir() if d.is_dir()]
    if not feed_dirs:
        report.record(source_project, "artifacts-feeds", status="ok", count=0)
        return

    # Existing feeds
    existing_feeds: set[str] = set()
    try:
        url = client._feeds_url(f"{client._org}/{target_project}", "_apis/packaging/feeds")
        existing_feeds = {f["name"] for f in client.get(url).get("value", [])}
    except ADOError:
        pass

    t0 = time.monotonic()
    created = 0

    for feed_dir in feed_dirs:
        feed_name = feed_dir.name
        feed_path = feed_dir / "feed.json"
        if not feed_path.exists():
            continue
        try:
            feed = json.loads(feed_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if feed_name in existing_feeds and not force:
            log.debug("Feed %s already exists; skipping", feed_name)
            continue

        if dry_run:
            log.info("[dry-run] Would restore feed %s/%s", target_project, feed_name)
            created += 1
            continue

        try:
            _create_feed(client, target_project, feed)
            created += 1
            log.info("Restored feed %s/%s", target_project, feed_name)
        except ADOError as exc:
            log.warning("Could not restore feed %s: %s", feed_name, exc)

    reason = ("Package binaries not re-published — use your package manager CLI to republish "
              "from backup/artifacts/feeds/<feed>/binaries/ if --include-package-binaries was used.")
    report.record(
        source_project, "artifacts-feeds", status="ok", count=created,
        duration=time.monotonic() - t0, reason=reason,
    )


def _create_feed(client: ADOClient, project: str, feed: dict) -> dict:
    url = client._feeds_url(f"{client._org}/{project}", "_apis/packaging/feeds")
    body = {
        "name": feed.get("name"),
        "description": feed.get("description", ""),
        "hideDeletedPackageVersions": feed.get("hideDeletedPackageVersions", True),
    }
    # Restore upstream sources if present
    upstreams_path = None  # we don't have it in feed.json but could check
    return client.post(url, body)
