"""Test plans exporter: plans, suites, cases, configurations, variables, runs."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ado_backup.client import ADOClient, ADOError
from ado_backup.checkpoint import Checkpoint, make_key

log = logging.getLogger(__name__)


def export_test_plans(
    client: ADOClient,
    project: str,
    project_dir: Path,
    manifest,
    checkpoint: Checkpoint,
    runs_per_test_plan: int = 100,
) -> None:
    tp_dir = project_dir / "test-plans"
    tp_dir.mkdir(parents=True, exist_ok=True)

    key = make_key(project, "test-plans")
    if checkpoint.is_done(key):
        return

    t0 = time.monotonic()
    try:
        url = client._dev_url(f"{project}/_apis/testplan/plans")
        data = client.get(url, {"$top": "500"})
        plans = data.get("value", [])
    except ADOError as exc:
        # Test Plans API may be unavailable on lower license tiers
        if "TF400813" in str(exc) or "402" in str(exc) or "403" in str(exc) or "404" in str(exc):
            log.warning("Test Plans not available for %s (license tier): %s", project, exc)
            manifest.record_item(
                project, "test-plans", status="skipped",
                reason="License tier does not include Test Plans, or project has none.",
            )
        else:
            log.warning("Could not fetch test plans for %s: %s", project, exc)
            manifest.record_item(project, "test-plans", status="error", reason=str(exc))
        return

    _write_json(tp_dir / "index.json", plans)

    for plan in plans:
        plan_id = plan.get("id")
        plan_dir = tp_dir / f"plan-{plan_id}"
        plan_dir.mkdir(exist_ok=True)
        _write_json(plan_dir / "plan.json", plan)

        # Test suites
        _export_suites(client, project, plan_id, plan_dir)

    # Test configurations
    _export_test_configurations(client, project, tp_dir)

    # Test variables
    _export_test_variables(client, project, tp_dir)

    # Test runs (last N)
    _export_test_runs(client, project, tp_dir, runs_per_test_plan)

    checkpoint.mark_done(key, count=len(plans))
    manifest.record_item(
        project, "test-plans", status="ok", count=len(plans), duration=time.monotonic() - t0
    )


def _export_suites(client: ADOClient, project: str, plan_id: int, plan_dir: Path) -> None:
    try:
        url = client._dev_url(f"{project}/_apis/testplan/plans/{plan_id}/suites")
        data = client.get(url, {"$top": "500", "$expand": "children"})
        suites = data.get("value", [])
        _write_json(plan_dir / "suites.json", suites)

        for suite in suites:
            suite_id = suite.get("id")
            _export_test_cases(client, project, plan_id, suite_id, plan_dir)
    except ADOError as exc:
        log.warning("Could not fetch suites for plan %s: %s", plan_id, exc)


def _export_test_cases(
    client: ADOClient, project: str, plan_id: int, suite_id: int, plan_dir: Path
) -> None:
    try:
        url = client._dev_url(f"{project}/_apis/testplan/plans/{plan_id}/suites/{suite_id}/testcase")
        data = client.get(url, {"$top": "500"})
        cases = data.get("value", [])
        if cases:
            _write_json(plan_dir / f"suite-{suite_id}-testcases.json", cases)
    except ADOError as exc:
        log.debug("Could not fetch test cases for suite %s: %s", suite_id, exc)


def _export_test_configurations(client: ADOClient, project: str, tp_dir: Path) -> None:
    try:
        url = client._dev_url(f"{project}/_apis/test/configurations")
        data = client.get(url)
        _write_json(tp_dir / "configurations.json", data)
    except ADOError as exc:
        log.warning("Could not fetch test configurations: %s", exc)


def _export_test_variables(client: ADOClient, project: str, tp_dir: Path) -> None:
    try:
        url = client._dev_url(f"{project}/_apis/test/variables")
        data = client.get(url)
        _write_json(tp_dir / "variables.json", data)
    except ADOError as exc:
        log.warning("Could not fetch test variables: %s", exc)


def _export_test_runs(client: ADOClient, project: str, tp_dir: Path, limit: int) -> None:
    try:
        url = client._dev_url(f"{project}/_apis/test/runs")
        data = client.get(url, {"$top": str(limit)})
        runs = data.get("value", [])
        _write_json(tp_dir / "runs.json", runs)
    except ADOError as exc:
        log.warning("Could not fetch test runs: %s", exc)


def _write_json(path: Path, data) -> int:
    text = json.dumps(data, indent=2, default=str)
    path.write_text(text)
    return len(text.encode())
