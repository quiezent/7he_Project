#!/usr/bin/env python3
"""Export only the named public methods; never copy private files or Git history.

This is an allowlist copier, not a content/secret scanner or publication clearance.
Run from a checked-out repository with Python 3.10+; no dependencies or network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

PUBLIC_FILES = (
    "README.md",
    "LESSONS.md",
    "ARCHITECTURE.md",
    "operating_model.json",
    "continuity.py",
    "tests/test_continuity.py",
)


def export_public(root: Path, output: Path) -> Path:
    """Refuse overwrite and symlinks; stage a complete archive before linking it."""
    root = root.resolve(strict=True)
    public = root / "public"
    if public.is_symlink() or not public.is_dir():
        raise ValueError("public must be an ordinary directory")
    payload: dict[str, bytes] = {}
    for relative in PUBLIC_FILES:
        source = public / relative
        for part in (source, *source.parents):
            if part == root:
                break
            if part.is_symlink():
                raise ValueError("symlinks are not allowed in the public export")
        if not source.is_file() or not source.resolve(strict=True).is_relative_to(public):
            raise ValueError("missing or invalid public file: " + relative)
        payload[relative] = source.read_bytes()
    manifest = {
        "scope": "allowlisted_methods_only_not_repository_history",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
        "not_a_privacy_or_authenticity_certificate": True,
    }
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("refusing to overwrite output: " + str(output))
    if not output.parent.is_dir():
        raise ValueError("output parent directory must already exist")
    if output.resolve().is_relative_to(public):
        raise ValueError("place the archive outside the public source directory")
    handle, temp_name = tempfile.mkstemp(prefix=".7he-public-", suffix=".zip", dir=output.parent)
    os.close(handle)
    temporary = Path(temp_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in payload.items():
                archive.writestr("7he-project-public/" + name, data)
            archive.writestr("7he-project-public/MANIFEST.json",
                             json.dumps(manifest, indent=2) + "\n")
        # Linking within the same directory atomically refuses an existing target.
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = export_public(Path(__file__).resolve().parents[1], args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, "export failed: " + str(exc) + "\n")
    print(result)


if __name__ == "__main__":
    main()
