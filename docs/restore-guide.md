# Restore Guide

`ado-backup restore` recreates backed-up resources in any target Azure DevOps organization.
This document covers per-category details, ordering, and common troubleshooting.

---

## Quickstart

```bash
# Full restore (all projects, all categories)
ado-backup restore backup.zip --org target-org --pat $PAT

# Specific projects
ado-backup restore backup.zip --org target-org --pat $PAT \
  --projects "Alpha,Beta"

# Specific categories
ado-backup restore backup.zip --org target-org --pat $PAT \
  --categories "repositories,work-items"

# Rename project on restore
ado-backup restore backup.zip --org target-org --pat $PAT \
  --map-project "OldProject=NewProject"

# Preview without making changes
ado-backup restore backup.zip --org target-org --pat $PAT --dry-run
```

---

## Category order and dependencies

The restorer processes categories in this order within each project:

1. **repositories** — must run before yaml-pipelines (YAML pipelines reference repo IDs)
2. **work-items** — must run before test-plans (test cases are work items)
3. **pipelines** — variable groups, task groups, environments, build/release/YAML defs
4. **wikis**
5. **boards** — teams, then dashboards
6. **artifacts** — feeds only; package binaries need manual republish
7. **test-plans** — uses work item ID map from step 2

If you use `--categories` to restore only a subset, be aware of these dependencies. For example, restoring `test-plans` without `work-items` means test cases won't be linked to the correct work items.

---

## Git Repositories

Each `repo.git/` directory is a bare mirror clone. The restorer:
1. Creates the repository in the target project via `POST /_apis/git/repositories`.
2. Runs `git push --mirror <auth-url>` to push all refs.

If the repo already exists and `--force` is not set, it is skipped. With `--force`, the push overwrites all refs.

**Troubleshooting:**
- `HTTP 403`: Your PAT needs **Code (Read & Write)** scope for the target org.
- `git push` failure: Check `run.log` for the error message. Ensure the PAT is valid for the target org.

---

## Work Items

Work items are recreated using the ADO PATCH API (JSON Patch operations). Two passes are used:

**Pass 1 — Create items:**
- Items are created in ascending ID order so parent items exist before children.
- System-managed fields (`System.Id`, timestamps, `System.Rev`, etc.) are skipped.
- Original created-by and created-date values are stored as `_original_CreatedBy` / `_original_CreatedDate` annotation fields so the history is still readable.
- State and other workflow fields are set as-is from the backup.

**Pass 2 — Restore relations:**
- All work item link relations (parent/child, related, duplicate, etc.) are re-added.
- Old work item IDs are remapped to new IDs using the mapping built in pass 1.
- Relations to work items that failed to create (or were outside the selected project scope) are silently skipped.

**Pass 3 — Comments:**
- Each backed-up comment is re-added. Because ADO doesn't allow setting comment author/date via API, the original author and date are prepended as italic text.

**Pass 4 — Attachments:**
- Binary attachment files are re-uploaded and linked to the restored work item.

**PAT scope required:** Work Items (Read & Write)

---

## Pipelines

Restored in this order within the `pipelines` category:

1. **Variable groups** — created first so definitions can reference them.
   - **Secret values are not restored** — they must be re-entered manually after restore.
2. **Task groups** — recreated as new versions.
3. **Environments** — pipeline deployment environments.
4. **Build definitions (classic)** — agent pool names are preserved by name; if the pool doesn't exist in the target org, the definition will have a broken pool reference.
5. **Release definitions** — service connections referenced by name; re-link after restore.
6. **YAML pipelines** — the pipeline definition references a YAML file path + repository. The repository must have been restored first. If the repository ID changed, the pipeline definition is created with the old repository ID reference and will need to be manually re-linked in the ADO UI.

**PAT scope required:** Build (Read & Write), Release (Read, Write & Execute), Variable Groups (Read, Create & Manage)

---

## Wikis

1. Creates the wiki via `POST /_apis/wiki/wikis` (project wiki or code wiki as recorded in the backup).
2. Runs `git push --mirror` to push the full wiki history.

For code wikis, the backing repository must be restored first and its ID updated in the wiki definition.

---

## Boards & Dashboards

**Teams** are created first. The default team is left as-is (ADO creates it automatically with the project).

**Dashboards** are recreated with their widget layouts. Widget data sources (queries, builds, releases) reference resources by ID. After restore, open each dashboard and reconfigure any widgets that show "widget could not load" — they need their data source re-pointed to the new IDs.

---

## Artifact Feeds

Feed definitions (name, description, views, upstream sources) are recreated. Package binaries are **not** automatically re-published. To republish:

1. Locate the package files in `artifacts/feeds/<feed>/binaries/` (only present if backed up with `--include-package-binaries`).
2. Use the appropriate package manager CLI:
   - npm: `npm publish`
   - NuGet: `nuget push`
   - PyPI: `twine upload`
   - Maven: `mvn deploy`

---

## Test Plans

Test plans, suites, and configurations are recreated. Test cases are linked to their restored work items using the ID mapping from the work-items restore pass. If work items were not restored in the same run, test case links will not be populated.

**PAT scope required:** Test Management (Read & Write)

---

## Project creation

By default (`--create-projects`), if the target project doesn't exist it is created with:
- Source control: Git
- Process template: Agile (default)

To use a different process template, create the project manually before running restore, then use `--no-create-projects`.

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `HTTP 403 Forbidden` | Missing PAT scope | Add the required scope (see per-category notes above) |
| `HTTP 400` on work item create | Invalid field value or type mismatch | Check `run.log` — the field name will be shown. The target project may use a different process template. |
| `HTTP 404` on pipeline create | Referenced agent pool or service connection doesn't exist | Create the pool/service connection first, then restore again with `--force` |
| YAML pipeline not linking to repo | Repository ID changed | Edit the pipeline definition in the ADO UI and re-select the repository |
| Dashboard widgets not loading | Data source IDs changed | Reconfigure widgets in the ADO UI |
| Test cases not linked | Work items not restored | Run restore with both `work-items` and `test-plans` categories |
| Secret variable values missing | Not restorable via API | Re-enter secrets manually in the variable group UI |
