# ado-backup

A production-grade backup tool for Azure DevOps. Given credentials and an organization, it produces a timestamped ZIP archive containing everything that can be exported from Azure DevOps via its public REST APIs: Git repositories (full history via `--mirror`), work items with attachments, pipelines, wikis, boards, dashboards, artifacts, and test plans.

---

## Quickstart

```bash
# Install
pip install ado-backup
# or from source
git clone https://github.com/skadad/ado-backup
pip install -e .

# Back up an entire organization
ado-backup backup --org my-org --pat $AZURE_DEVOPS_PAT

# Back up specific projects only
ado-backup backup --org my-org --pat $PAT --projects "ProjectA,ProjectB"

# Verify a produced archive
ado-backup verify ado-backup-my-org-20240101T000000Z.zip
```

The archive is written to `./backups/ado-backup-<org>-<timestamp>.zip` by default.

---

## Authentication

Credentials are resolved in this order:

| Priority | Method | How |
|----------|--------|-----|
| 1 | PAT | `--pat <token>` or `AZURE_DEVOPS_PAT` env var |
| 2 | Azure CLI | Automatic when `az login` has been run |
| 3 | Service Principal | `AZURE_CLIENT_ID` + `AZURE_TENANT_ID` + `AZURE_CLIENT_SECRET` env vars, or `--service-principal` flag |

### Required PAT scopes

Create a PAT at `https://dev.azure.com/<org>/_usersSettings/tokens` with the following scopes (all **Read**):

| Scope | Required for |
|-------|-------------|
| Code (Read) | Git repositories, pull requests |
| Work Items (Read) | Work items, queries, area paths, iterations |
| Build (Read) | Build definitions, pipeline runs |
| Release (Read) | Release definitions |
| Test Management (Read) | Test plans, suites, runs |
| Packaging (Read) | Artifact feeds and packages |
| Wiki (Read) | Wiki repos and page trees |
| Project and Team (Read) | Projects, teams, dashboards |
| Identity (Read) | Users, groups |
| Graph (Read) | Graph API for users/groups |
| Service Connections (Read) | Service endpoint metadata |
| Variable Groups (Read) | Variable group names/non-secret values |
| Environment (Read) | Pipeline environments |
| Extensions (Read) | Installed extensions list |

> **Tip:** If in doubt, create the PAT with "Full access" scope for a one-time backup, then rotate it.

---

## Full CLI Reference

```
ado-backup backup [OPTIONS]

Options:
  --org TEXT                    ADO organization name or URL  [required]
  --pat TEXT                    Personal Access Token (or AZURE_DEVOPS_PAT)
  --use-az-cli                  Authenticate via Azure CLI
  --service-principal           Authenticate via service principal
  --projects TEXT               Comma-separated project names (default: all)
  --output-dir TEXT             Output directory [default: ./backups]
  --concurrency INT             Parallel project workers [default: 4]
  --include-logs                Download pipeline run logs (large!)
  --include-package-binaries    Download artifact package binaries (large!)
  --runs-per-pipeline INT       Max pipeline run records [default: 200]
  --runs-per-test-plan INT      Max test run records [default: 100]
  --no-zip                      Skip ZIP creation (for debugging)
  --zip-format-zip64            Force ZIP64 format
  --force                       Re-run everything, ignoring checkpoints
  --dry-run                     List what would be backed up without doing it
  --log-format [text|json]      Log format [default: text]
  --log-level TEXT              Log level [default: INFO]
  --help                        Show this message and exit.

ado-backup verify ARCHIVE

Arguments:
  ARCHIVE  Path to ZIP archive to verify  [required]

Options:
  -v, --verbose  Show per-item status
  --help         Show this message and exit.
```

---

## What is backed up

| Category | What | Notes |
|----------|------|-------|
| Organization | Name, ID, region, projects list | |
| Users & Groups | Graph API users and groups | |
| Service Connections | Metadata only (names, types, project scopes) | Secrets not retrievable |
| Agent Pools | Pool definitions | Agent tokens not retrievable |
| Extensions | Installed extension names + versions | |
| Git Repositories | Full `--mirror` clone (all branches, tags, history) | |
| Pull Requests | Full detail: description, reviewers, votes, threads, linked WIs | Closed/abandoned included |
| Branch Policies | Policy configurations per repo | |
| Work Items | All items with all fields, relations, history (revisions), comments | |
| WI Attachments | Binary attachment files | |
| Queries | Shared and personal queries | |
| Area Paths | Full area tree | |
| Iterations | Full iteration tree | |
| Dashboards | Layout + widget configuration | Widget live data not restorable |
| Teams | Members, iterations, board columns, swim lanes, settings | |
| Process Templates | Inherited process definitions | System processes (Agile/Scrum/CMMI/Basic) recorded by name only |
| Build Definitions | Classic and YAML build definitions | |
| Release Definitions | Release pipeline definitions | |
| YAML Pipelines | Unified pipeline definitions | |
| Pipeline Run History | Last N runs per pipeline (metadata) | Logs off by default; enable with `--include-logs` |
| Variable Groups | Names, variable names, non-secret values | Secret values recorded as `null, isSecret: true` |
| Task Groups | Task group definitions | |
| Deployment Groups | Deployment group definitions | |
| Environments | Pipeline environment definitions | |
| Artifact Feeds | Feed metadata, packages list, views, upstream sources | |
| Package Binaries | Latest version of each package | Off by default; `--include-package-binaries` |
| Test Plans | Plans, suites, test cases, configurations, variables | Requires Basic + Test Plans license |
| Test Runs | Last N run metadata | |
| Wikis | Full `--mirror` clone + page tree JSON | |
| Project Metadata | Description, visibility, capabilities, process reference | |

For a full list of what is **not** backed up, see [docs/what-is-not-backed-up.md](docs/what-is-not-backed-up.md).

---

## Output structure

```
backups/
└── ado-backup-<org>-<timestamp>/
    ├── manifest.json         <- single source of truth about the run
    ├── run.log
    ├── .checkpoints.jsonl    <- resume data (hidden file)
    ├── organization/
    │   ├── organization.json
    │   ├── projects.json
    │   ├── users.json
    │   ├── groups.json
    │   ├── service-connections.json
    │   ├── agent-pools.json
    │   └── extensions.json
    └── projects/
        └── <project-name>/
            ├── project.json
            ├── teams/
            ├── repositories/
            │   └── <repo>/
            │       ├── repo.json
            │       ├── repo.git/        <- bare mirror clone
            │       ├── pull-requests/
            │       └── policies.json
            ├── work-items/
            │   ├── index.json
            │   ├── items/<id>.json
            │   └── attachments/<id>/
            ├── queries/
            ├── areas.json
            ├── iterations.json
            ├── dashboards/
            ├── process/
            ├── pipelines/
            │   ├── builds/
            │   ├── releases/
            │   ├── yaml/
            │   ├── variable-groups/
            │   ├── task-groups/
            │   ├── environments/
            │   └── runs/
            ├── artifacts/feeds/
            ├── test-plans/
            └── wikis/
                └── <wiki>/
                    ├── wiki.git/
                    └── pages.json
```

The entire tree is then zipped into `ado-backup-<org>-<timestamp>.zip`.

---

## Resume / idempotency

Every completed work unit is recorded in `.checkpoints.jsonl`. Re-running the tool into the same `--output-dir` will skip already-completed work. Use `--force` to re-do everything.

---

## Pipeline setup

See [pipelines/azure-pipelines.yml](pipelines/azure-pipelines.yml) for a ready-to-use pipeline.

**Service principal setup (recommended):**

1. Create an Azure AD service principal with `Project Collection Administrator` (or broad Reader + Test Plans + Packaging) at the org level.
2. Add a service connection in ADO of type "Azure Resource Manager" or use a PAT stored in a secret variable group / Key Vault.
3. In the pipeline YAML, set `AZURE_DEVOPS_PAT` from the service connection or secret, or use `System.AccessToken` for a single-project backup (note: `System.AccessToken` is too narrow for org-wide reads in most setups).

**`System.AccessToken` limitations:** The built-in token only has access to the current project and cannot enumerate the full organization. For a full org backup you need a PAT or service principal.

---

## Restore guidance

This tool is a backup tool, not a restore tool. The backup format is designed to be usable with each ADO API's import/migration endpoints:

| Data | Restore path |
|------|-------------|
| Git repos | `git push --mirror` to a new ADO repo |
| Work items | Work Items - Create REST API; consider azure-devops-migration-tools |
| Build definitions | Definitions - Create REST API |
| Release definitions | Release Management REST API |
| Wikis | `git push --mirror` to a new wiki repo |
| Test plans | Test Plan - Create REST API |

See [docs/restore-guide.md](docs/restore-guide.md) for more detail.

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| HTTP 401 | Expired PAT or wrong token | Regenerate PAT with required scopes |
| HTTP 403 | Missing scope | Add missing scope to PAT (see Required PAT scopes above) |
| HTTP 429 | Rate limited | Tool handles this automatically with `Retry-After` backoff |
| Project not found | Typo in `--projects` | Check project name is an exact match (case-insensitive) |
| `git clone` failure | Wrong remote URL or network issue | Check `run.log` for the error; ensure git is on PATH |
| Test plans skipped | License tier | Requires Basic + Test Plans license |
| Project deleted mid-run | Race condition | The project entry will have `status: error` in manifest |

---

## Development

```bash
git clone https://github.com/skadad/ado-backup
pip install -e ".[dev]"

# Unit tests (no credentials needed)
pytest tests/unit/

# Integration tests (requires real ADO org)
export ADO_TEST_ORG=my-org
export ADO_TEST_PAT=mytoken
pytest -m integration
```
