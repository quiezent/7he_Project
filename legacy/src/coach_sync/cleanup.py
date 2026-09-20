from __future__ import annotations

import shutil
from pathlib import Path

from .paths import repo_root


SAFE_CACHE_NAMES = {"__pycache__", ".pytest_cache"}


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
    return {"apply": apply, "removed_or_would_remove": sorted(removed)}

