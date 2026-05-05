"""Work items exporter: items, revisions, comments, attachments, queries, areas, iterations."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from ado_backup.client import ADOClient, ADOError
from ado_backup.checkpoint import Checkpoint, make_key

log = logging.getLogger(__name__)

_BATCH_SIZE = 200  # ADO max for workitemsbatch


def export_work_items(
    client: ADOClient,
    project: str,
    project_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    concurrency: int = 4,
) -> None:
    wi_dir = project_dir / "work-items"
    wi_dir.mkdir(parents=True, exist_ok=True)
    items_dir = wi_dir / "items"
    items_dir.mkdir(exist_ok=True)
    attachments_dir = wi_dir / "attachments"
    attachments_dir.mkdir(exist_ok=True)

    t0 = time.monotonic()

    # ---- Work item IDs ----
    ids_key = make_key(project, "work-items-ids")
    ids_path = wi_dir / "index.json"

    try:
        if not checkpoint.is_done(ids_key):
            ids = client.list_work_item_ids(project)
            _write_json(ids_path, {"count": len(ids), "ids": ids})
            checkpoint.mark_done(ids_key, count=len(ids))
        else:
            existing = json.loads(ids_path.read_text())
            ids = existing["ids"]
    except ADOError as exc:
        log.error("Could not fetch work item IDs for %s: %s", project, exc)
        manifest.record_item(project, "work-items", status="error", reason=str(exc))
        return

    if not ids:
        manifest.record_item(project, "work-items", status="ok", count=0, duration=time.monotonic() - t0)
        return

    # ---- Fetch each item (batched, then individual detail) ----
    pending_ids = [i for i in ids if not checkpoint.is_done(make_key(project, "wi", str(i)))]

    def _fetch_and_save(item_id: int) -> bool:
        key = make_key(project, "wi", str(item_id))
        if checkpoint.is_done(key):
            return True
        out = items_dir / f"{item_id}.json"
        try:
            # Full detail with all expands
            url = client._dev_url(f"{project}/_apis/wit/workitems/{item_id}")
            item = client.get(url, {"$expand": "all"})

            revisions = client.get_work_item_revisions(project, item_id)
            comments = client.get_work_item_comments(project, item_id)
            item["_revisions"] = revisions
            item["_comments"] = comments

            _write_json(out, item)

            # Download attachments
            _download_attachments(client, item, item_id, attachments_dir)

            checkpoint.mark_done(key)
            return True
        except ADOError as exc:
            log.warning("Could not fetch work item %s: %s", item_id, exc)
            return False

    failed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_fetch_and_save, wid): wid for wid in pending_ids}
        for fut in as_completed(futures):
            if not fut.result():
                failed += 1

    success_count = len(ids) - failed
    status = "ok" if failed == 0 else ("error" if failed == len(ids) else "ok")
    manifest.record_item(
        project, "work-items", status=status,
        count=success_count, duration=time.monotonic() - t0,
        reason=f"{failed} items failed" if failed else "",
    )

    # ---- Queries ----
    _export_queries(client, project, project_dir, manifest, checkpoint)

    # ---- Area paths ----
    _export_areas(client, project, project_dir, manifest, checkpoint)

    # ---- Iterations ----
    _export_iterations(client, project, project_dir, manifest, checkpoint)


def _download_attachments(client: ADOClient, item: dict, item_id: int, attachments_dir: Path) -> None:
    relations = item.get("relations") or []
    for rel in relations:
        if rel.get("rel") != "AttachedFile":
            continue
        url = rel.get("url", "")
        fname = rel.get("attributes", {}).get("name", "attachment")
        fname = _safe_filename(fname)
        dest_dir = attachments_dir / str(item_id)
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / fname
        if dest.exists():
            continue
        try:
            data = client.get_raw(url, {})
            dest.write_bytes(data)
        except (ADOError, OSError) as exc:
            log.warning("Could not download attachment %s for WI %s: %s", fname, item_id, exc)


def _export_queries(client: ADOClient, project: str, project_dir: Path, manifest, checkpoint: Checkpoint) -> None:
    key = make_key(project, "queries")
    if checkpoint.is_done(key):
        return
    queries_dir = project_dir / "queries"
    queries_dir.mkdir(exist_ok=True)
    try:
        url = client._dev_url(f"{project}/_apis/wit/queries")
        data = client.get(url, {"$depth": "2", "$expand": "all"})
        _write_json(queries_dir / "queries.json", data)
        checkpoint.mark_done(key)
        manifest.record_item(project, "queries", status="ok")
    except ADOError as exc:
        log.warning("Could not fetch queries for %s: %s", project, exc)
        manifest.record_item(project, "queries", status="error", reason=str(exc))


def _export_areas(client: ADOClient, project: str, project_dir: Path, manifest, checkpoint: Checkpoint) -> None:
    key = make_key(project, "areas")
    if checkpoint.is_done(key):
        return
    try:
        url = client._dev_url(f"{project}/_apis/wit/classificationnodes/areas")
        data = client.get(url, {"$depth": "10"})
        _write_json(project_dir / "areas.json", data)
        checkpoint.mark_done(key)
        manifest.record_item(project, "areas", status="ok")
    except ADOError as exc:
        log.warning("Could not fetch areas for %s: %s", project, exc)
        manifest.record_item(project, "areas", status="error", reason=str(exc))


def _export_iterations(client: ADOClient, project: str, project_dir: Path, manifest, checkpoint: Checkpoint) -> None:
    key = make_key(project, "iterations")
    if checkpoint.is_done(key):
        return
    try:
        url = client._dev_url(f"{project}/_apis/wit/classificationnodes/iterations")
        data = client.get(url, {"$depth": "10"})
        _write_json(project_dir / "iterations.json", data)
        checkpoint.mark_done(key)
        manifest.record_item(project, "iterations", status="ok")
    except ADOError as exc:
        log.warning("Could not fetch iterations for %s: %s", project, exc)
        manifest.record_item(project, "iterations", status="error", reason=str(exc))


def _safe_filename(name: str) -> str:
    name = name.strip()
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    while ".." in name:
        name = name.replace("..", "_")
    name = name.strip(".")
    return name[:200] or "attachment"


def _write_json(path: Path, data) -> int:
    text = json.dumps(data, indent=2, default=str)
    path.write_text(text)
    return len(text.encode())
