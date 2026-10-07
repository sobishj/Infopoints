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


def source_header(file_name: str, label: str) -> str:
    return f"[Source: {file_name} | {label}]"
