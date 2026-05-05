"""Configuration model for ado-backup."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BackupConfig:
    # Auth
    org: str
    pat: Optional[str] = None
    use_az_cli: bool = False
    use_service_principal: bool = False
    client_id: Optional[str] = None
    tenant_id: Optional[str] = None
    client_secret: Optional[str] = None

    # Scope
    projects: list[str] = field(default_factory=list)  # empty = all

    # Output
    output_dir: str = "./backups"
    no_zip: bool = False
    zip_format_zip64: bool = False
    force: bool = False
    dry_run: bool = False

    # Parallelism
    concurrency: int = 4

    # Optional content
    include_logs: bool = False
    include_package_binaries: bool = False
    runs_per_pipeline: int = 200
    runs_per_test_plan: int = 100

    # Logging
    log_format: str = "text"  # text | json
    log_level: str = "INFO"

    @classmethod
    def from_env_and_args(cls, **kwargs) -> "BackupConfig":
        """Merge CLI arguments with environment variables."""
        pat = kwargs.pop("pat", None) or os.environ.get("AZURE_DEVOPS_PAT")
        client_id = kwargs.pop("client_id", None) or os.environ.get("AZURE_CLIENT_ID")
        tenant_id = kwargs.pop("tenant_id", None) or os.environ.get("AZURE_TENANT_ID")
        client_secret = kwargs.pop("client_secret", None) or os.environ.get("AZURE_CLIENT_SECRET")

        org = kwargs.get("org", "")
        # Normalize org: strip trailing slash, extract name from URL
        if org.startswith("https://dev.azure.com/"):
            org = org.rstrip("/").split("/")[-1]
        elif org.startswith("https://"):
            org = org.rstrip("/").split("/")[-1]
        kwargs["org"] = org

        return cls(
            pat=pat,
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
            **{k: v for k, v in kwargs.items() if v is not None},
        )

    def to_safe_dict(self) -> dict:
        """Return config as dict with secrets redacted."""
        d = {
            "org": self.org,
            "pat": "REDACTED" if self.pat else None,
            "use_az_cli": self.use_az_cli,
            "use_service_principal": self.use_service_principal,
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "client_secret": "REDACTED" if self.client_secret else None,
            "projects": self.projects,
            "output_dir": self.output_dir,
            "no_zip": self.no_zip,
            "force": self.force,
            "dry_run": self.dry_run,
            "concurrency": self.concurrency,
            "include_logs": self.include_logs,
            "include_package_binaries": self.include_package_binaries,
            "runs_per_pipeline": self.runs_per_pipeline,
            "runs_per_test_plan": self.runs_per_test_plan,
            "log_format": self.log_format,
            "log_level": self.log_level,
        }
        return d
