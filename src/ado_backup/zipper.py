"""ZIP archive creation for the backup output directory."""

from __future__ import annotations

import logging
import os
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)


def zip_directory(source_dir: Path, dest_zip: Path, force_zip64: bool = False) -> int:
    """Zip source_dir into dest_zip. Returns total uncompressed bytes."""
    total_bytes = 0

    # Determine if zip64 is needed
    if not force_zip64:
        for root, dirs, files in os.walk(source_dir):
            # Skip bare git repos (already in the zip via walk)
            for fname in files:
                total_bytes += (Path(root) / fname).stat().st_size
        use_zip64 = total_bytes > 2 * 1024 ** 3  # 2 GiB
        total_bytes = 0  # reset for actual zip pass
    else:
        use_zip64 = True

    log.info("Creating archive %s (zip64=%s)", dest_zip, use_zip64)

    with zipfile.ZipFile(
        dest_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=use_zip64,
    ) as zf:
        for root, dirs, files in os.walk(source_dir):
            # Sort for deterministic ordering
            dirs.sort()
            files.sort()
            for fname in files:
                fpath = Path(root) / fname
                arcname = fpath.relative_to(source_dir.parent)
                try:
                    zf.write(fpath, arcname)
                    total_bytes += fpath.stat().st_size
                except OSError as exc:
                    log.warning("Could not add %s to archive: %s", fpath, exc)

    log.info(
        "Archive complete: %s (%.1f MiB uncompressed, %.1f MiB compressed)",
        dest_zip,
        total_bytes / 1024 ** 2,
        dest_zip.stat().st_size / 1024 ** 2,
    )
    return total_bytes


def verify_archive(zip_path: Path) -> tuple[bool, list[str]]:
    """Verify archive integrity. Returns (ok, list_of_errors)."""
    errors = []

    if not zip_path.exists():
        return False, [f"Archive not found: {zip_path}"]

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                errors.append(f"First bad file in archive: {bad}")
    except zipfile.BadZipFile as exc:
        return False, [f"Bad ZIP file: {exc}"]

    return len(errors) == 0, errors
