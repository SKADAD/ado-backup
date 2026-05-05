"""Unit tests for work items restore logic."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from ado_backup.restorers.work_items import (
    restore_work_items,
    _create_work_item,
    _add_relations,
    _SKIP_FIELDS,
    _WI_RELATION_TYPES,
)
from ado_backup.client import ADOError


def _make_client(org="myorg"):
    client = MagicMock()
    client._org = org
    client._dev_url = lambda path: f"https://dev.azure.com/{org}/{path}"
    client._headers = {"Authorization": "Basic dG9rZW4="}
    return client


def _make_report():
    report = MagicMock()
    return report


def _make_item(item_id: int, title: str = "Test", wi_type: str = "Task") -> dict:
    return {
        "id": item_id,
        "fields": {
            "System.Id": item_id,
            "System.WorkItemType": wi_type,
            "System.Title": title,
            "System.Description": "desc",
            "System.State": "Active",
            "System.AreaPath": "MyProject",
        },
        "relations": [],
        "_comments": [],
    }


def test_restore_skips_missing_directory(tmp_path):
    client = _make_client()
    report = _make_report()
    restore_work_items(client, "Src", "Tgt", tmp_path / "nonexistent", report)
    report.record.assert_called_once()
    call_kwargs = report.record.call_args
    assert call_kwargs[1]["status"] == "skipped"


def test_restore_empty_items_dir(tmp_path):
    wi_dir = tmp_path / "work-items" / "items"
    wi_dir.mkdir(parents=True)
    report = _make_report()
    client = _make_client()
    restore_work_items(client, "Src", "Tgt", tmp_path, report)
    report.record.assert_called()
    # Should record ok with count=0
    calls = [c for c in report.record.call_args_list if c[1].get("status") == "ok"]
    assert calls


def test_dry_run_does_not_call_api(tmp_path):
    wi_dir = tmp_path / "work-items" / "items"
    wi_dir.mkdir(parents=True)
    item = _make_item(42)
    (wi_dir / "42.json").write_text(json.dumps(item))

    client = _make_client()
    report = _make_report()
    id_map = restore_work_items(client, "Src", "Tgt", tmp_path, report, dry_run=True)

    client.patch.assert_not_called()
    assert id_map == {}


def test_create_work_item_builds_patch_ops():
    client = _make_client()
    client.patch.return_value = {"id": 99}

    item = _make_item(1, title="My Task")
    new_id = _create_work_item(client, "MyProject", item)

    assert new_id == 99
    client.patch.assert_called_once()
    args = client.patch.call_args
    ops = args[0][1]  # second positional arg is the ops list
    # Title must be in ops
    titles = [op for op in ops if op["path"] == "/fields/System.Title"]
    assert titles
    assert titles[0]["value"] == "My Task"
    # System.Id must NOT be in ops (server-managed)
    ids = [op for op in ops if op["path"] == "/fields/System.Id"]
    assert not ids


def test_skip_fields_not_in_patch_ops():
    client = _make_client()
    client.patch.return_value = {"id": 1}

    item = {
        "id": 1,
        "fields": {
            "System.WorkItemType": "Task",
            "System.Title": "title",
            **{f: "value" for f in _SKIP_FIELDS},
        },
        "relations": [],
        "_comments": [],
    }
    _create_work_item(client, "P", item)
    ops = client.patch.call_args[0][1]
    op_paths = {op["path"] for op in ops}
    for skip in _SKIP_FIELDS:
        assert f"/fields/{skip}" not in op_paths, f"{skip} should not be in ops"


def test_add_relations_remaps_ids():
    client = _make_client()
    client.patch.return_value = {"id": 10}

    id_map = {100: 200}
    relations = [{
        "rel": "System.LinkTypes.Related",
        "url": "https://dev.azure.com/myorg/_apis/wit/workitems/100",
        "attributes": {},
    }]
    _add_relations(client, "P", 10, relations, id_map)

    client.patch.assert_called_once()
    ops = client.patch.call_args[0][1]
    assert len(ops) == 1
    assert "/workitems/200" in ops[0]["value"]["url"]


def test_add_relations_skips_unmapped_ids():
    client = _make_client()
    id_map = {}  # no mapping
    relations = [{
        "rel": "System.LinkTypes.Related",
        "url": "https://dev.azure.com/myorg/_apis/wit/workitems/999",
        "attributes": {},
    }]
    _add_relations(client, "P", 10, relations, id_map)
    client.patch.assert_not_called()


def test_restore_work_items_full_flow(tmp_path):
    wi_dir = tmp_path / "work-items" / "items"
    wi_dir.mkdir(parents=True)

    item1 = _make_item(1, "Item 1")
    item2 = _make_item(2, "Item 2")
    # item2 has a relation to item1
    item2["relations"] = [{
        "rel": "System.LinkTypes.Hierarchy-Reverse",
        "url": "https://dev.azure.com/x/_apis/wit/workitems/1",
        "attributes": {},
    }]

    (wi_dir / "1.json").write_text(json.dumps(item1))
    (wi_dir / "2.json").write_text(json.dumps(item2))

    client = _make_client()
    client.patch.side_effect = [{"id": 101}, {"id": 102}, {"id": 102}]

    report = _make_report()
    id_map = restore_work_items(client, "Src", "Tgt", tmp_path, report)

    assert id_map[1] == 101
    assert id_map[2] == 102
    # patch called: create item1, create item2, add relation for item2
    assert client.patch.call_count == 3
