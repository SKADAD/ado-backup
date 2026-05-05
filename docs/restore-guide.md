# Restore Guide

`ado-backup` produces a backup, not a restore mechanism. This document explains the conceptual restore path for each data type. Actual restoration requires careful planning and is outside the scope of this tool.

---

## Git Repositories

Each `repo.git/` directory is a bare mirror clone.

```bash
# Create a new repo in ADO, then:
cd projects/MyProject/repositories/my-repo/repo.git
git push --mirror https://dev.azure.com/<new-org>/<new-project>/_git/<new-repo>
```

This restores all branches, tags, and full commit history.

---

## Wikis

Wiki repos are stored the same way as Git repos:

```bash
cd projects/MyProject/wikis/my-wiki/wiki.git
git push --mirror https://dev.azure.com/<new-org>/<new-project>/_git/<wiki-repo-id>
```

---

## Work Items

Work item restore via REST is possible but complex. Each field must be set explicitly and system fields (like `Created Date`) require special bypass flags. Consider:

- The [azure-devops-migration-tools](https://github.com/nkdAgility/azure-devops-migration-tools) project for full WI migration.
- The [Work Items - Create](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/create) REST API for scripted import.
- The `items/<id>.json` files contain the full field set needed.

Work item attachments are in `work-items/attachments/<id>/`. Re-upload them via the [Attachments - Create](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/attachments/create) API, then link them to the restored work item.

---

## Pipelines

Build and release definitions can be created via the REST API:
- [Build Definitions - Create](https://learn.microsoft.com/en-us/rest/api/azure/devops/build/definitions/create)
- [Release Definitions - Create](https://learn.microsoft.com/en-us/rest/api/azure/devops/release/definitions/create)

YAML pipelines reference a YAML file in a repository. Restore the repository first, then create the pipeline definition pointing at the correct YAML path.

Variable groups can be recreated via [Variable Groups - Add](https://learn.microsoft.com/en-us/rest/api/azure/devops/distributedtask/variablegroups/add). Secret values must be re-entered manually.

---

## Test Plans

Restore test plans via the [Test Plan REST API](https://learn.microsoft.com/en-us/rest/api/azure/devops/testplan/). Plans → Suites → Test Cases must be created in order. The `test-plans/` directory contains the full hierarchy.

---

## Artifact Feeds

Feeds can be recreated via the [Feeds - Create](https://learn.microsoft.com/en-us/rest/api/azure/devops/artifacts/feed/create) API. If `--include-package-binaries` was used, packages can be re-published to the new feed using the appropriate package manager CLI (`npm publish`, `nuget push`, `pip upload`, etc.).

---

## A note on IDs

ADO work item IDs, pipeline run IDs, and similar identifiers are organization-specific. A restored project will have different IDs. Cross-references (e.g., work item links, PR descriptions mentioning `#123`) will need to be updated manually or via a migration script.
