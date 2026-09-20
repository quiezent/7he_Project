from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def repo_root(root: str | Path | None = None) -> Path:
    return Path(root).resolve() if root is not None else ROOT


def config_dir(root: str | Path | None = None) -> Path:
    return repo_root(root) / "config"


def snapshots_dir(root: str | Path | None = None) -> Path:
    return repo_root(root) / "snapshots"


def activities_dir(root: str | Path | None = None) -> Path:
    return repo_root(root) / "activities"


def input_dir(root: str | Path | None = None) -> Path:
    return repo_root(root) / "input"


def archive_dir(root: str | Path | None = None) -> Path:
    return repo_root(root) / "archive"


def context_path(root: str | Path | None = None) -> Path:
    return config_dir(root) / "athlete_context.json"


def daily_checkin_path(root: str | Path | None = None) -> Path:
    return input_dir(root) / "daily_checkin.md"


def ensure_layout(root: str | Path | None = None) -> None:
    for path in (
        config_dir(root),
        snapshots_dir(root),
        activities_dir(root),
        input_dir(root),
        archive_dir(root),
    ):
        path.mkdir(parents=True, exist_ok=True)

