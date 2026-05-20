from pathlib import Path


def resolve_under(base_dir: Path, *parts: str) -> Path:
    """Resolve a child path and ensure it stays inside base_dir."""

    base = base_dir.resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"path escapes base directory: {target}")
    return target


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
