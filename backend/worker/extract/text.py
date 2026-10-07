"""TXT by line windows, Markdown by heading sections (location = heading + line range)."""
import re

from worker.extract import Extracted, ExtractionError, Segment

TXT_LINES_PER_SEGMENT = 60
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _read(path: str) -> list[str]:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ExtractionError(f"Cannot read file: {e}") from e
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace").splitlines()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    raise ExtractionError("Unknown text encoding.")


def extract_markdown(path: str) -> Extracted:
    lines = _read(path)
    segments: list[Segment] = []
    heading, start, buf = None, 1, []

    def flush(end: int):
        text = "\n".join(buf).strip()
        if text:
            segments.append(Segment(text=text, loc_type="heading" if heading else "lines", heading=heading,
                                    line_start=start, line_end=end))

    for n, line in enumerate(lines, 1):
        m = _HEADING.match(line)
        if m:
            flush(n - 1)
            heading, start, buf = m.group(2), n, [line]
        else:
            buf.append(line)
    flush(len(lines))
    return Extracted(segments=segments)


def extract_text(path: str) -> Extracted:
    lines = _read(path)
    segments = []
    for i in range(0, len(lines), TXT_LINES_PER_SEGMENT):
        block = lines[i:i + TXT_LINES_PER_SEGMENT]
        text = "\n".join(block).strip()
        if text:
            segments.append(Segment(text=text, loc_type="lines", line_start=i + 1, line_end=i + len(block)))
    return Extracted(segments=segments)
