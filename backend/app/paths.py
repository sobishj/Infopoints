"""Host/container path resolution with traversal guards, bound to the configured roots."""
import os
from pathlib import PurePosixPath

from app import rootmap
from app.config import get_settings


class PathNotAllowed(ValueError):
    pass


def roots() -> list[rootmap.Root]:
    return get_settings().roots


def resolve_host_path(host_path: str) -> tuple[str, str, str]:
    """Validate a user-supplied host path. Returns (normalized host path, root alias, container path)."""
    if not host_path or not host_path.strip():
        raise PathNotAllowed("Enter a folder path.")
    found = rootmap.find_root(host_path, roots())
    if found is None:
        allowed = ", ".join(r.host for r in roots()) or "(none configured)"
        raise PathNotAllowed(
            f"This path is outside the allowed document roots ({allowed}). "
            "To add a root, append it to ALLOWED_DOC_ROOTS in .env and restart InfoPoint."
        )
    root, rest = found
    container = rootmap.host_to_container(host_path, roots(), get_settings().roots_mount_base)
    return rootmap.normalize_host_path(host_path), root.alias, container


def container_to_host(container_path: str) -> str | None:
    return rootmap.container_to_host(container_path, roots(), get_settings().roots_mount_base)


def safe_join(base: str, rel_path: str) -> str:
    """Join a stored relative path under base, refusing anything that escapes it."""
    base_real = os.path.realpath(base)
    full = os.path.realpath(os.path.join(base_real, *PurePosixPath(rel_path).parts))
    if full != base_real and not full.startswith(base_real + os.sep):
        raise PathNotAllowed("Path escapes the document folder.")
    return full
