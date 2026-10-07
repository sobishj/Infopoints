"""Mapping between host paths (what users see and paste) and container paths.

Stdlib only: scripts/gen_roots.py imports this on the host to generate the compose mounts.

Each allowed root gets a stable alias and is mounted read-only at <base>/<alias>:
    C:\\                    -> /roots/c
    D:\\Data\\Projects      -> /roots/d_data_projects
    \\\\fileserver\\projects -> /roots/unc_fileserver_projects
    /mnt/shares            -> /roots/mnt_shares
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_DRIVE = re.compile(r"^[A-Za-z]:")


def is_windows_path(p: str) -> bool:
    return bool(_DRIVE.match(p)) or p.startswith("\\\\") or p.startswith("//")


def _parts(p: str) -> tuple[str, list[str]]:
    """Split a host path into (kind, components). kind is 'drive', 'unc' or 'posix'."""
    p = p.strip().strip('"').strip("'")
    if p.startswith("\\\\") or (p.startswith("//") and is_windows_path(p)):
        comps = [c for c in re.split(r"[\\/]+", p[2:]) if c]
        return "unc", comps
    if _DRIVE.match(p):
        comps = [c for c in re.split(r"[\\/]+", p) if c]
        comps[0] = comps[0].upper()
        return "drive", comps
    return "posix", [c for c in p.split("/") if c]


def _clean(comps: list[str]) -> list[str] | None:
    """Reject traversal components; drop '.'."""
    out = []
    for c in comps:
        if c == ".":
            continue
        if c == "..":
            return None
        out.append(c)
    return out


def normalize_host_path(p: str) -> str:
    kind, comps = _parts(p)
    if kind == "drive":
        return comps[0] + "\\" + "\\".join(comps[1:])
    if kind == "unc":
        return "\\\\" + "\\".join(comps)
    return "/" + "/".join(comps)


def root_alias(root: str) -> str:
    kind, comps = _parts(root)
    if kind == "drive":
        comps = [comps[0][0]] + comps[1:]
    elif kind == "unc":
        comps = ["unc"] + comps
    alias = "_".join(comps).lower() if comps else "root"
    return re.sub(r"[^a-z0-9_]+", "_", alias).strip("_") or "root"


@dataclass(frozen=True)
class Root:
    host: str
    alias: str
    kind: str

    def mount_source(self) -> str:
        """Bind-mount source for docker compose (forward slashes work on Docker Desktop)."""
        if self.kind == "drive":
            return self.host.replace("\\", "/")
        return self.host


def parse_roots(spec: str) -> list[Root]:
    roots, seen = [], set()
    for raw in (spec or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        kind, _ = _parts(raw)
        host = normalize_host_path(raw)
        alias = root_alias(host)
        base, n = alias, 2
        while alias in seen:
            alias, n = f"{base}_{n}", n + 1
        seen.add(alias)
        roots.append(Root(host=host, alias=alias, kind=kind))
    return roots


def _key(kind: str, comps: list[str]) -> list[str]:
    # Windows paths compare case-insensitively.
    return [c.lower() for c in comps] if kind in ("drive", "unc") else comps


def find_root(host_path: str, roots: list[Root]) -> tuple[Root, list[str]] | None:
    """Return (root, remaining components) for the most specific root containing host_path."""
    kind, comps = _parts(host_path)
    comps = _clean(comps)
    if comps is None:
        return None
    best = None
    for r in roots:
        rkind, rcomps = _parts(r.host)
        if rkind != kind or len(rcomps) > len(comps):
            continue
        if _key(kind, comps[: len(rcomps)]) == _key(kind, rcomps):
            if best is None or len(rcomps) > best[1]:
                best = (r, len(rcomps))
    if best is None:
        return None
    return best[0], comps[best[1]:]


def host_to_container(host_path: str, roots: list[Root], base: str = "/roots") -> str | None:
    found = find_root(host_path, roots)
    if found is None:
        return None
    root, rest = found
    return "/".join([base.rstrip("/"), root.alias, *rest])


def container_to_host(container_path: str, roots: list[Root], base: str = "/roots") -> str | None:
    prefix = base.rstrip("/") + "/"
    if not container_path.startswith(prefix):
        return None
    comps = [c for c in container_path[len(prefix):].split("/") if c]
    if not comps or _clean(comps) is None:
        return None
    root = next((r for r in roots if r.alias == comps[0]), None)
    if root is None:
        return None
    rest = comps[1:]
    if root.kind == "posix":
        return "/".join([root.host.rstrip("/"), *rest]) if rest else root.host
    sep = "\\"
    return root.host.rstrip(sep) + (sep + sep.join(rest) if rest else ("" if root.kind == "unc" else sep))
