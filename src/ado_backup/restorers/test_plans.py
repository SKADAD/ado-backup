"""Test plans restorer: plans, suites, test cases."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from ado_backup.client import ADOClient, ADOError

log = logging.getLogger(__name__)


def restore_test_plans(
    client: ADOClient,
    source_project: str,
    target_project: str,
    project_backup_dir: Path,
    report,
    wi_id_map: Optional[dict[int, int]] = None,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    tp_dir = project_backup_dir / "test-plans"
    if not tp_dir.exists():
        report.record(source_project, "test-plans", status="skipped", reason="No test-plans directory in backup")
        return

    index_path = tp_dir / "index.json"
    if not index_path.exists():
        report.record(source_project, "test-plans", status="ok", count=0)
        return

    try:
        plans_index = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        report.record(source_project, "test-plans", status="error", reason="Could not read test plan index")
        return

    # Check if test plans API is available
    try:
        url = client._dev_url(f"{target_project}/_apis/testplan/plans")
        client.get(url, {"$top": "1"})
    except ADOError as exc:
        report.record(
            source_project, "test-plans", status="skipped",
            reason=f"Test Plans API not available: {exc}",
        )
        return

    t0 = time.monotonic()
    created = 0
    wi_id_map = wi_id_map or {}

    for plan_stub in plans_index:
        plan_id = plan_stub.get("id")
        plan_dir = tp_dir / f"plan-{plan_id}"
        plan_path = plan_dir / "plan.json" if plan_dir.exists() else None

        plan_data = plan_stub
        if plan_path and plan_path.exists():
            try:
                plan_data = json.loads(plan_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        name = plan_data.get("name", f"Plan {plan_id}")

        if dry_run:
            log.info("[dry-run] Would restore test plan %s/%s", target_project, name)
            created += 1
            continue

        try:
            new_plan = _create_test_plan(client, target_project, plan_data)
            new_plan_id = new_plan.get("id")
            created += 1

            # Restore suites
            suites_path = plan_dir / "suites.json" if plan_dir else None
            if suites_path and suites_path.exists():
                _restore_suites(client, target_project, new_plan_id, plan_id, plan_dir, wi_id_map)

        except ADOError as exc:
            log.warning("Could not restore test plan %s: %s", name, exc)

    report.record(source_project, "test-plans", status="ok", count=created, duration=time.monotonic() - t0)


def _create_test_plan(client: ADOClient, project: str, plan: dict) -> dict:
    url = client._dev_url(f"{project}/_apis/testplan/plans")
    area = plan.get("area", {})
    iteration = plan.get("iteration", "")
    body = {
        "name": plan.get("name", "Restored Plan"),
        "area": {"name": area.get("name", "")} if area else {},
        "iteration": iteration,
        "description": plan.get("description", ""),
        "startDate": plan.get("startDate"),
        "endDate": plan.get("endDate"),
    }
    # Remove None values
    body = {k: v for k, v in body.items() if v is not None}
    return client.post(url, body)


def _restore_suites(
    client: ADOClient,
    project: str,
    new_plan_id: int,
    old_plan_id: int,
    plan_dir: Path,
    wi_id_map: dict[int, int],
) -> None:
    suites_path = plan_dir / "suites.json"
    if not suites_path.exists():
        return
    try:
        suites = json.loads(suites_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    for suite in suites:
        old_suite_id = suite.get("id")
        suite_type = suite.get("suiteType", "staticTestSuite")
        name = suite.get("name", f"Suite {old_suite_id}")

        try:
            url = client._dev_url(f"{project}/_apis/testplan/plans/{new_plan_id}/suites")
            body = {"name": name, "suiteType": suite_type}
            result = client.post(url, body)
            new_suite_id = result.get("id")

            # Restore test cases into this suite
            cases_path = plan_dir / f"suite-{old_suite_id}-testcases.json"
            if cases_path.exists() and wi_id_map:
                _restore_test_cases_to_suite(client, project, new_plan_id, new_suite_id, cases_path, wi_id_map)

        except ADOError as exc:
            log.debug("Could not restore suite %s: %s", name, exc)


def _restore_test_cases_to_suite(
    client: ADOClient,
    project: str,
    plan_id: int,
    suite_id: int,
    cases_path: Path,
    wi_id_map: dict[int, int],
) -> None:
    try:
        cases = json.loads(cases_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    new_ids = []
    for case in cases:
        old_wi_id = case.get("workItem", {}).get("id")
        if old_wi_id and old_wi_id in wi_id_map:
            new_ids.append(wi_id_map[old_wi_id])

    if not new_ids:
        return

    try:
        url = client._dev_url(f"{project}/_apis/testplan/plans/{plan_id}/suites/{suite_id}/testcase")
        # ADO expects a list of {workItem: {id: ...}}
        body = [{"workItem": {"id": nid}} for nid in new_ids]
        client.post(url, body)
    except ADOError as exc:
        log.debug("Could not add test cases to suite %s: %s", suite_id, exc)
