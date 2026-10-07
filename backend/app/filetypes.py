"""Supported file types. Media kinds are recognised now; their extractor arrives in Phase 2."""
import os

KIND_BY_EXT = {
    ".pdf": "pdf",
    ".docx": "office", ".doc": "office", ".odt": "office", ".rtf": "office",
    ".pptx": "office", ".ppt": "office", ".odp": "office",
    ".txt": "text", ".md": "text", ".markdown": "text",
}
MEDIA_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".mp3", ".wav", ".m4a"}
SLIDE_EXTS = {".pptx", ".ppt", ".odp"}

# Types offered as filters in the folder form.
TYPE_GROUPS = {
    "pdf": [".pdf"],
    "word": [".docx", ".doc", ".odt", ".rtf"],
    "powerpoint": [".pptx", ".ppt", ".odp"],
    "text": [".txt", ".md", ".markdown"],
}

# Rough CPU cost per file, used only for the folder preview estimate.
_SECONDS_PER_MB = {"pdf": 2.5, "office": 4.0, "text": 1.0}
_SECONDS_PER_FILE = {"pdf": 3.0, "office": 8.0, "text": 0.5}


def kind_for(filename: str) -> str | None:
    return KIND_BY_EXT.get(os.path.splitext(filename)[1].lower())


def is_ignored_name(name: str) -> bool:
    """Office lock files, temp files and hidden files."""
    return name.startswith(("~$", ".~lock", ".")) or name.endswith((".tmp", ".crdownload", ".part"))


def allowed_exts(filter_groups: list[str] | None) -> set[str]:
    if not filter_groups:
        return set(KIND_BY_EXT)
    exts: set[str] = set()
    for g in filter_groups:
        exts.update(TYPE_GROUPS.get(g, [g if g.startswith(".") else "." + g]))
    return exts & set(KIND_BY_EXT)


def estimate_seconds(kind: str, size_bytes: int) -> float:
    return _SECONDS_PER_FILE.get(kind, 2.0) + _SECONDS_PER_MB.get(kind, 2.0) * size_bytes / 1_048_576
