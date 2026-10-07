"""Extractors turn a file into location-tagged segments. Chunks never cross a segment."""
from dataclasses import dataclass, field


class ExtractionError(Exception):
    """The file itself can't be processed (corrupt, encrypted, unsupported). Not retried."""


@dataclass
class Segment:
    text: str
    loc_type: str                  # page | slide | lines | heading | sheet_rows | time
    page: int | None = None        # page or slide number (1-based)
    line_start: int | None = None
    line_end: int | None = None
    heading: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Extracted:
    segments: list[Segment]
    page_count: int | None = None
    derived_pdf_path: str | None = None
