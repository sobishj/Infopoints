"""Section headings for PDF text, so every passage knows where it sits ("3 Authentication › 3.1 RequestToken").

Repetitive documents (API references, manuals) mention the same terms on every page; the section path is
what tells search that pages 10–12 are *the* authentication chapter. Uses the PDF outline when present,
otherwise numbered headings found in the text:
    "3 Authentication"            one line
    "3.1" + "RequestToken"        number and title on separate lines (common in Word exports)
Numbered list items ("1. Returns True."), table-of-contents lines ("9.3 GETDOCUMENT ....... 142") and numbers
that go backwards (stray digits in body text) are ignored.
"""
import re

_NUM_TITLE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s+([A-Za-z][^\n]{1,80})$")
_NUM_ONLY = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,3})$")
_TITLE = re.compile(r"^[A-Za-z][\w &/,\-–()'’]{1,80}$")
MAX_TOP_LEVEL = 40


def _key(number: str) -> tuple[int, ...]:
    return tuple(int(p) for p in number.split("."))


class SectionTracker:
    def __init__(self):
        self.path: list[tuple[str, str]] = []  # [(number, title), ...] from top level down

    @property
    def current(self) -> str | None:
        return " › ".join(f"{n} {t}".strip() for n, t in self.path) or None

    def accepts(self, number: str) -> bool:
        k = _key(number)
        if k[0] == 0 or k[0] > MAX_TOP_LEVEL:
            return False
        if not self.path:
            return True
        return k > _key(self.path[-1][0])  # headings only move forward through the document

    def enter(self, number: str, title: str) -> None:
        depth = number.count(".") + 1
        self.path = [p for p in self.path if p[0].count(".") + 1 < depth] + [(number, title.strip())]

    def enter_outline(self, level: int, title: str) -> None:
        self.path = self.path[: level - 1] + [("", title.strip())]


def _heading_at(lines: list[str], i: int) -> tuple[str, str, int] | None:
    """(number, title, lines consumed) if a heading starts at line i."""
    line = lines[i].strip()
    if "...." in line or line.endswith((".", ":", ",", ";")):
        return None
    m = _NUM_TITLE.match(line)
    if m and len(m.group(2).split()) <= 8:
        return m.group(1), m.group(2).strip(), 1
    if _NUM_ONLY.match(line) and i + 1 < len(lines):
        title = lines[i + 1].strip()
        if _TITLE.match(title) and "...." not in title and not title.endswith((".", ":")):
            return line, title, 2
    return None


def split_by_headings(text: str, tracker: SectionTracker) -> list[tuple[str | None, str]]:
    """Split one page into [(section path, text)], updating the tracker as headings appear."""
    lines = text.splitlines()
    parts: list[tuple[str | None, str]] = []
    buf: list[str] = []
    section = tracker.current
    i = 0
    while i < len(lines):
        h = _heading_at(lines, i)
        if h and tracker.accepts(h[0]):
            if "".join(buf).strip():
                parts.append((section, "\n".join(buf).strip()))
            number, title, used = h
            tracker.enter(number, title)
            section = tracker.current
            buf = [f"{number} {title}"]
            i += used
            continue
        buf.append(lines[i])
        i += 1
    if "".join(buf).strip():
        parts.append((section, "\n".join(buf).strip()))
    return parts


def is_toc_page(text: str) -> bool:
    return text.count("....") >= 5
