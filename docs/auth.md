# Authentication Guide

## Personal Access Token (PAT) — recommended for most cases

1. Go to `https://dev.azure.com/<org>/_usersSettings/tokens`
2. Click "New Token"
3. Set the expiry to match your backup retention period
4. Grant **Read** scope on: Code, Work Items, Build, Release, Test Management, Packaging, Wiki, Project and Team, Identity, Graph, Service Connections, Variable Groups, Environment, Extensions
5. Copy the token immediately (it is not shown again)
6. Pass as `--pat <token>` or set `AZURE_DEVOPS_PAT=<token>`

**Security:** The token is transmitted as an HTTP Basic Authentication header (Base64-encoded) over HTTPS. It is never written to disk, logs, or stdout. The manifest records `"pat": "REDACTED"`.

## Azure CLI

```bash
az login
ado-backup backup --org my-org --use-az-cli
```

The tool runs `az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798` and uses the resulting Bearer token. Works well in environments where the Azure CLI is already configured (developer machines, Azure Cloud Shell).

## Service Principal (MSAL)

For unattended/automated use without a PAT:

```bash
export AZURE_CLIENT_ID=<app-id>
export AZURE_TENANT_ID=<tenant-id>
export AZURE_CLIENT_SECRET=<secret>
ado-backup backup --org my-org --service-principal
```

The service principal needs at minimum:
- **Project Collection Administrator** at the organization level, OR
- **Reader** at every project + **Test Plans Contributor** + **Packaging Reader** + **Build Reader** etc.

The tool exchanges client credentials for an ADO bearer token using the OAuth2 client-credentials flow via MSAL.

## Pipeline: using System.AccessToken

```yaml
- script: |
    export AZURE_DEVOPS_PAT=$(System.AccessToken)
    ado-backup backup --org $(System.TeamFoundationCollectionUri) --pat $(System.AccessToken)
```

**Limitation:** `System.AccessToken` is scoped to the current project and pipeline. It cannot enumerate other projects in the organization. For an org-wide backup, use a PAT or service principal stored in a secret variable group.

## Credential precedence

1. `--pat` flag
2. `AZURE_DEVOPS_PAT` environment variable
3. Azure CLI (`az account get-access-token`)
4. Service principal env vars (`AZURE_CLIENT_ID` + `AZURE_TENANT_ID` + `AZURE_CLIENT_SECRET`)

If none are found, the tool exits with an error and a clear message.
