"""Authentication helpers for Azure DevOps.

Precedence:
1. PAT (--pat / AZURE_DEVOPS_PAT env var)
2. Azure CLI  (az account get-access-token)
3. Service principal  (MSAL client-credentials)
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Optional

log = logging.getLogger(__name__)

_ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"


class AuthError(Exception):
    pass


def get_auth_header(config) -> dict[str, str]:
    """Return an Authorization header dict for the given config."""
    token = _resolve_token(config)
    return {"Authorization": f"Bearer {token}"}


def _resolve_token(config) -> str:
    if config.pat:
        log.debug("Using PAT authentication")
        import base64
        encoded = base64.b64encode(f":{config.pat}".encode()).decode()
        # Return a pseudo-bearer that the client will handle as Basic auth
        # We use a sentinel to signal PAT mode
        return f"__PAT__:{config.pat}"

    if config.use_az_cli or (not config.use_service_principal):
        token = _try_az_cli()
        if token:
            log.debug("Using Azure CLI authentication")
            return token

    if config.use_service_principal or (
        config.client_id and config.tenant_id and config.client_secret
    ):
        return _msal_token(config)

    raise AuthError(
        "No credentials found. Provide --pat, set AZURE_DEVOPS_PAT, "
        "run `az login`, or set AZURE_CLIENT_ID/AZURE_TENANT_ID/AZURE_CLIENT_SECRET."
    )


def _try_az_cli() -> Optional[str]:
    try:
        result = subprocess.run(
            [
                "az",
                "account",
                "get-access-token",
                "--resource",
                _ADO_RESOURCE,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data["accessToken"]
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
        pass
    return None


def _msal_token(config) -> str:
    try:
        import msal
    except ImportError:
        raise AuthError("msal package is required for service principal auth. pip install msal")

    if not (config.client_id and config.tenant_id and config.client_secret):
        raise AuthError(
            "Service principal auth requires AZURE_CLIENT_ID, AZURE_TENANT_ID, "
            "and AZURE_CLIENT_SECRET (or --service-principal with matching env vars)."
        )

    authority = f"https://login.microsoftonline.com/{config.tenant_id}"
    app = msal.ConfidentialClientApplication(
        config.client_id,
        authority=authority,
        client_credential=config.client_secret,
    )
    result = app.acquire_token_for_client(scopes=[f"{_ADO_RESOURCE}/.default"])
    if "access_token" not in result:
        raise AuthError(
            f"MSAL token acquisition failed: {result.get('error_description', result)}"
        )
    log.debug("Using service principal (MSAL) authentication")
    return result["access_token"]


def build_request_headers(config) -> dict[str, str]:
    """Build full set of headers for ADO requests."""
    import base64

    raw_token = _resolve_token(config)

    if raw_token.startswith("__PAT__:"):
        pat = raw_token[len("__PAT__:"):]
        encoded = base64.b64encode(f":{pat}".encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
        }
    else:
        return {
            "Authorization": f"Bearer {raw_token}",
            "Content-Type": "application/json",
        }
