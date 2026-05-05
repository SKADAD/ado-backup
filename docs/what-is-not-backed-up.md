# What is NOT backed up

This document explicitly lists categories of data that `ado-backup` cannot or does not export, and why.

---

## Secrets (by design — ADO does not expose them)

| Item | Why |
|------|-----|
| Service connection secret credentials | ADO's API only returns metadata (name, type, project scope). The actual username/password/token/certificate stored in the service connection is never returned by any API endpoint. This is intentional security behavior. |
| Variable group secret values | Secret variables are write-only in ADO. The API returns the variable name and `isSecret: true`, but the value is always redacted as empty. `ado-backup` records these with `"value": null, "isSecret": true`. |
| Agent registration tokens | Agent tokens are single-use and never re-readable after creation. |
| PATs and OAuth tokens | Credential management is outside the scope of a backup tool. |
| Key Vault-linked variable values | Variables linked to Azure Key Vault are resolved at pipeline runtime. The backup captures the Key Vault reference (vault name + secret name), not the secret value. |

---

## Pipeline run logs

Pipeline run logs can be very large (many GB for busy organizations). They are **off by default**. Enable with `--include-logs` to download them. Even with the flag, only the last 50 runs per pipeline are downloaded to avoid excessive storage use.

---

## Audit log

The ADO Audit Log is a separate API surface (`_apis/audit/auditlog`) that requires an **Auditing** license add-on. It is not included in `ado-backup` v1. To export audit logs, use the ADO UI (`Organization Settings > Auditing`) or the dedicated REST endpoint.

---

## Pipeline artifacts and test attachments

Build artifacts (the files produced by a pipeline run and uploaded as artifacts) are not downloaded. Only the run metadata is captured. This is because pipeline artifacts can be enormous and are better managed with a dedicated artifact retention policy.

Test result attachments (screenshots, videos, HAR files from test runs) are similarly excluded.

---

## Identity-bound settings

| Item | Notes |
|------|-------|
| Personal notification subscriptions | Bound to individual user identities |
| Personal PATs of other users | Never accessible via API |
| User-specific dashboard settings | Some dashboard widget preferences are per-user |
| Personal queries | Captured where the PAT owner has access; other users' personal queries are not accessible |

---

## Retention policies (applied, not just configured)

`ado-backup` captures the *configuration* of retention policies, but not the artifacts that have already been deleted by them.

---

## TFVC repositories (partial)

TFVC changeset history is exported as JSON via the REST API. However, the full binary content of shelvesets is not downloaded. For full TFVC migration, use `tf.exe` or the Git-TF tooling.

---

## Environments: approval history and deployment records

Pipeline environment definitions are backed up. Deployment records (which runs deployed to which environment, and who approved) are not included in v1.

---

## Extension data (custom storage)

Some ADO extensions store private data in the Extension Data Service (`_apis/ExtensionManagement/InstalledExtensions/<publisher>/<ext>/Data/Scopes`). This is extension-specific and not included.

---

## Cross-org relationships

Links between work items in different organizations, or references to resources outside the backed-up organization, are captured as metadata (URLs/IDs) but the referenced resources are not followed.
