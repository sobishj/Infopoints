import re

"""Human-readable location labels and source headers, shared by indexing and answering."""


def fmt_ts(seconds: float | None) -> str:
    s = int(seconds or 0)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def loc_label(loc_type: str, page: int | None = None, line_start: int | None = None, line_end: int | None = None,
              heading: str | None = None, sheet: str | None = None, row_start: int | None = None,
              row_end: int | None = None, t_start: float | None = None, t_end: float | None = None) -> str:
    if loc_type == "page":
        return f"Page {page}"
    if loc_type == "slide":
        return f"Slide {page}"
    if loc_type == "time":
        return f"{fmt_ts(t_start)}–{fmt_ts(t_end)}"
    if loc_type == "sheet_rows":
        return f"Sheet {sheet}, rows {row_start}–{row_end}"
    if loc_type == "heading" and heading:
        return f"Section “{heading}”, lines {line_start}–{line_end}"
    return f"Lines {line_start}–{line_end}"


def source_header(file_name: str, label: str, section: str | None = None) -> str:
    return f"[Source: {file_name} | {label}" + (f" | {section}]" if section else "]")


def section_of(loc_type: str, heading: str | None) -> str | None:
    """Section path shown next to page/slide locations (headings of text files are already in the label)."""
    return heading if loc_type in ("page", "slide") and heading else None


def section_title_text(section: str) -> str:
    """How a section path is embedded for question → section matching: numbers dropped, camelCase split
    ("3 Authentication › 3.1 RequestToken" → "Authentication › Request Token"). Measured on an API
    reference, this separates look-alike titles (RequestToken vs RequestTokenChangeMailAddress) far better."""
    parts = [re.sub(r"^\d+(\.\d+)*\s+", "", p) for p in section.split(" › ")]
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", " › ".join(parts))