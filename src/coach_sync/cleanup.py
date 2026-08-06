from __future__ import annotations

import shutil
from pathlib import Path

from .paths import repo_root, snapshots_dir


SAFE_CACHE_NAMES = {"__pycache__", ".pytest_cache"}
TRANSIENT_SNAPSHOT_PATTERNS = (
    "activity_loop_load_current.json",
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def cleanup_derived(root: str | Path | None = None, apply: bool = False) -> dict:
    base = repo_root(root)
    candidates: list[Path] = []
    for name in SAFE_CACHE_NAMES:
        candidates.extend(base.glob(f"**/{name}"))

    removed = []
    for path in candidates:
        if any(part in {"activities", "DI_CONNECT", "DI_CONNECT_IQ", "customer_data"} for part in path.parts):
            continue
        removed.append(str(path))
        if apply:
            shutil.rmtree(path, ignore_errors=True)

    snapshot_base = snapshots_dir(base)
    for pattern in TRANSIENT_SNAPSHOT_PATTERNS:
        for path in snapshot_base.glob(pattern):
            resolved = path.resolve()
            if not resolved.is_file() or not _is_within(resolved, snapshot_base):
                continue
            removed.append(str(resolved))
            if apply:
                resolved.unlink(missing_ok=True)

    return {"apply": apply, "removed_or_would_remove": sorted(removed)}
