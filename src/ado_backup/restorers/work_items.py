"""Work items restorer.

Two-pass strategy:
  Pass 1 — create every work item with its scalar fields; build old→new ID map.
  Pass 2 — re-add relations, re-mapping old IDs to new IDs.
  Pass 3 — add comments.

System-managed fields (timestamps, server-set values) are skipped; we preserve
them as custom "_original_*" fields so the history is still readable.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from ado_backup.client import ADOClient, ADOError

log = logging.getLogger(__name__)

# Fields that ADO sets server-side; trying to set them produces 400 errors.
_SKIP_FIELDS = {
    "System.Id",
    "System.Rev",
    "System.AreaId",
    "System.IterationId",
    "System.RevisedDate",
    "System.Watermark",
    "System.AuthorizedAs",
    "System.AuthorizedDate",
    "System.ChangedDate",
    "System.NodeName",
    "Microsoft.VSTS.Common.StateChangeDate",
    "Microsoft.VSTS.Common.ActivatedDate",
    "Microsoft.VSTS.Common.ResolvedDate",
    "Microsoft.VSTS.Common.ClosedDate",
    "Microsoft.VSTS.Common.ActivatedBy",
    "Microsoft.VSTS.Common.ResolvedBy",
    "Microsoft.VSTS.Common.ClosedBy",
}

# Fields we store as "_original_*" annotations (informational only)
_PRESERVE_AS_ANNOTATION = {
    "System.CreatedDate",
    "System.CreatedBy",
    "System.ChangedBy",
    "System.ChangedDate",
}

# Relations that link to other work items (need ID remapping)
_WI_RELATION_TYPES = {
    "System.LinkTypes.Hierarchy-Forward",
    "System.LinkTypes.Hierarchy-Reverse",
    "System.LinkTypes.Related",
    "System.LinkTypes.Dependency-Forward",
    "System.LinkTypes.Dependency-Reverse",
    "System.LinkTypes.Duplicate-Forward",
    "System.LinkTypes.Duplicate-Reverse",
    "System.LinkTypes.Tested-Forward",
    "System.LinkTypes.Tested-Reverse",
    "Microsoft.VSTS.Common.TestedBy-Forward",
    "Microsoft.VSTS.Common.TestedBy-Reverse",
    "Microsoft.VSTS.Common.Affects-Forward",
    "Microsoft.VSTS.Common.Affects-Reverse",
}


def restore_work_items(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    report,
    force: bool = False,
    dry_run: bool = False,
) -> dict[int, int]:
    """Restore work items. Returns old_id → new_id mapping."""
    wi_dir = project_backup_dir / "work-items"
    if not wi_dir.exists():
        report.record(source_project, "work-items", status="skipped", reason="No work-items directory in backup")
        return {}

    items_dir = wi_dir / "items"
    if not items_dir.exists() or not any(items_dir.iterdir()):
        report.record(source_project, "work-items", status="ok", count=0)
        return {}

    # Load all items sorted by ID (ascending) to preserve parent-child order
    item_files = sorted(items_dir.glob("*.json"), key=lambda p: int(p.stem))
    items = []
    for f in item_files:
        try:
            items.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read %s: %s", f, exc)

    if not items:
        report.record(source_project, "work-items", status="ok", count=0)
        return {}

    if dry_run:
        log.info("[dry-run] Would restore %d work items into %s", len(items), target_project)
        report.record(source_project, "work-items", status="ok", count=len(items), reason="dry-run")
        return {}

    t0 = time.monotonic()

    # ---- Pass 1: create items ----
    id_map: dict[int, int] = {}  # old_id → new_id
    created = 0
    failed = 0

    for item in items:
        old_id = item.get("id")
        wi_type = _get_field(item, "System.WorkItemType")
        if not wi_type:
            log.warning("Work item %s has no type; skipping", old_id)
            failed += 1
            continue

        try:
            new_id = _create_work_item(client, target_project, item)
            if new_id:
                id_map[old_id] = new_id
                created += 1
        except ADOError as exc:
            log.warning("Could not create work item %s (%s): %s", old_id, wi_type, exc)
            failed += 1

    log.info("Work items pass 1: created=%d failed=%d", created, failed)

    # ---- Pass 2: add relations ----
    relations_ok = 0
    relations_failed = 0
    for item in items:
        old_id = item.get("id")
        if old_id not in id_map:
            continue
        new_id = id_map[old_id]
        relations = item.get("relations") or []
        wi_relations = [r for r in relations if r.get("rel") in _WI_RELATION_TYPES]
        if not wi_relations:
            continue
        try:
            _add_relations(client, target_project, new_id, wi_relations, id_map)
            relations_ok += len(wi_relations)
        except ADOError as exc:
            log.debug("Could not add relations for WI %s: %s", new_id, exc)
            relations_failed += len(wi_relations)

    if relations_failed:
        log.warning("Work item relations: %d added, %d failed", relations_ok, relations_failed)

    # ---- Pass 3: restore comments ----
    _restore_comments(client, target_project, items, id_map)

    # ---- Restore attachments ----
    attachments_dir = wi_dir / "attachments"
    if attachments_dir.exists():
        _restore_attachments(client, source_project, target_project, attachments_dir, id_map, report)

    status = "ok" if failed == 0 else ("error" if created == 0 else "ok")
    reason = f"{failed} items failed to create" if failed else ""
    report.record(
        source_project, "work-items", status=status, count=created,
        duration=time.monotonic() - t0, reason=reason,
    )
    return id_map


def _create_work_item(client: ADOClient, project: str, item: dict) -> Optional[int]:
    wi_type = _get_field(item, "System.WorkItemType")
    fields = item.get("fields", {})

    ops = []

    # Core fields in priority order
    for field_name, value in fields.items():
        if field_name in _SKIP_FIELDS:
            continue
        if value is None:
            continue
        # Store original timestamps/authors as annotations
        if field_name in _PRESERVE_AS_ANNOTATION:
            ops.append({"op": "add", "path": f"/fields/_original_{field_name.split('.')[-1]}", "value": str(value)})
            continue
        ops.append({"op": "add", "path": f"/fields/{field_name}", "value": value})

    if not ops:
        return None

    # Ensure title is present
    if not any(op["path"] == "/fields/System.Title" for op in ops):
        return None

    url = client._dev_url(f"{project}/_apis/wit/workitems/${wi_type}")
    result = client.patch(url, ops, content_type="application/json-patch+json")
    return result.get("id")


def _add_relations(
    client: ADOClient, project: str, new_id: int, relations: list[dict], id_map: dict[int, int]
) -> None:
    ops = []
    for rel in relations:
        old_url = rel.get("url", "")
        # Extract old work item ID from the relation URL
        try:
            old_target_id = int(old_url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            continue
        new_target_id = id_map.get(old_target_id)
        if not new_target_id:
            continue
        # Build the new relation URL for the target org
        new_url = client._dev_url(f"_apis/wit/workitems/{new_target_id}")
        ops.append({
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": rel["rel"],
                "url": new_url,
                "attributes": rel.get("attributes", {}),
            },
        })

    if ops:
        url = client._dev_url(f"{project}/_apis/wit/workitems/{new_id}")
        client.patch(url, ops, content_type="application/json-patch+json")


def _restore_comments(
    client: ADOClient, project: str, items: list[dict], id_map: dict[int, int]
) -> None:
    for item in items:
        old_id = item.get("id")
        new_id = id_map.get(old_id)
        if not new_id:
            continue
        comments = item.get("_comments", [])
        for comment in comments:
            text = comment.get("text", "")
            if not text:
                continue
            author = comment.get("createdBy", {}).get("displayName", "")
            timestamp = comment.get("createdDate", "")
            # Prefix with original author/date since we can't set those server-side
            body_text = f"*[Original comment by {author} on {timestamp}]*\n\n{text}"
            try:
                url = client._dev_url(f"{project}/_apis/wit/workitems/{new_id}/comments")
                client.post(url, {"text": body_text})
            except ADOError as exc:
                log.debug("Could not restore comment for WI %s: %s", new_id, exc)


def _restore_attachments(
    client: ADOClient,
    source_project: str,
    target_project: str,
    attachments_dir: Path,
    id_map: dict[int, int],
    report,
) -> None:
    for id_dir in attachments_dir.iterdir():
        if not id_dir.is_dir():
            continue
        try:
            old_id = int(id_dir.name)
        except ValueError:
            continue
        new_id = id_map.get(old_id)
        if not new_id:
            continue
        for attachment_file in id_dir.iterdir():
            if not attachment_file.is_file():
                continue
            try:
                # Upload attachment
                upload_url = client._dev_url(
                    f"{target_project}/_apis/wit/attachments"
                )
                headers = dict(client._headers)
                headers["Content-Type"] = "application/octet-stream"
                import httpx
                resp = client._http.post(
                    upload_url,
                    params={"api-version": "7.1", "fileName": attachment_file.name},
                    headers=headers,
                    content=attachment_file.read_bytes(),
                )
                if resp.status_code not in (200, 201):
                    log.debug("Could not upload attachment %s: HTTP %s", attachment_file.name, resp.status_code)
                    continue
                att_data = resp.json()
                att_url = att_data.get("url", "")
                if not att_url:
                    continue
                # Link to work item
                link_url = client._dev_url(f"{target_project}/_apis/wit/workitems/{new_id}")
                ops = [{
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "AttachedFile",
                        "url": att_url,
                        "attributes": {"name": attachment_file.name},
                    },
                }]
                client.patch(link_url, ops, content_type="application/json-patch+json")
            except (ADOError, OSError) as exc:
                log.debug("Could not restore attachment %s: %s", attachment_file.name, exc)


def _get_field(item: dict, field_name: str):
    return item.get("fields", {}).get(field_name)
