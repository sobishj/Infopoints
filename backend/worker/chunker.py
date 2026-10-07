"""Token-sized chunks (~450 tokens, ~50 overlap) that never cross a segment (page/slide/section) boundary."""
import re
from dataclasses import dataclass
from functools import lru_cache

from app.config import get_settings
from app.embed.models import get_spec
from app.locations import loc_label, section_of, source_header
from worker.extract import Segment

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


class _Counter:
    def __init__(self):
        path = get_settings().embedding_model_path / "tokenizer.json"
        self.tok = None
        if path.exists():
            from tokenizers import Tokenizer
            self.tok = Tokenizer.from_file(str(path))

    def counts(self, texts: list[str]) -> list[int]:
        if not texts:
            return []
        if self.tok is not None:
            return [len(e.ids) for e in self.tok.encode_batch(texts, add_special_tokens=False)]
        # Fallback when the model isn't installed (unit tests): ~1.3 tokens per word.
        return [max(1, round(len(t.split()) * 1.3)) for t in texts]


@lru_cache
def counter() -> _Counter:
    return _Counter()


@dataclass
class ChunkDraft:
    ordinal: int
    text: str
    token_count: int
    segment: Segment
    label: str
    header: str


def _units(text: str) -> list[str]:
    """Paragraphs, further split into sentences when a paragraph is long."""
    units = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) > 600:
            units.extend(s.strip() for s in _SENTENCE.split(para) if s.strip())
        else:
            units.append(para)
    return units


def _split_long(unit: str, tokens: int, max_tokens: int) -> list[str]:
    words = unit.split()
    per_piece = max(1, int(len(words) * max_tokens / tokens * 0.9))
    return [" ".join(words[i:i + per_piece]) for i in range(0, len(words), per_piece)]


def chunk_segment_texts(text: str, max_tokens: int, overlap: int) -> list[tuple[str, int]]:
    c = counter()
    units = _units(text)
    sizes = c.counts(units)
    expanded: list[tuple[str, int]] = []
    for u, n in zip(units, sizes):
        if n > max_tokens:
            pieces = _split_long(u, n, max_tokens)
            expanded.extend(zip(pieces, c.counts(pieces)))
        else:
            expanded.append((u, n))

    chunks: list[tuple[str, int]] = []
    current: list[tuple[str, int]] = []
    total = 0
    for u, n in expanded:
        if current and total + n > max_tokens:
            chunks.append(("\n\n".join(x for x, _ in current), total))
            carry, carried = [], 0
            for x, m in reversed(current):  # overlap: trailing units up to `overlap` tokens
                if carried + m > overlap:
                    break
                carry.insert(0, (x, m))
                carried += m
            current, total = (carry, carried) if carried + n <= max_tokens else ([], 0)
        current.append((u, n))
        total += n
    if current:
        chunks.append(("\n\n".join(x for x, _ in current), total))
    return chunks


MIN_LETTERS = 3  # a page number ("6") has no searchable content


def has_content(text: str, heading: str | None = None) -> bool:
    """False for passages that would only waste a search slot: a bare page number, or just the section
    heading itself ("2 Common Methods") with nothing under it."""
    if sum(ch.isalpha() for ch in text) < MIN_LETTERS:
        return False
    words = " ".join(text.split()).lower()
    title = " ".join((heading or "").split(" › ")[-1].split()).lower()
    return not title or words != title


def chunk_segments(segments: list[Segment], file_name: str) -> list[ChunkDraft]:
    s = get_settings()
    # Leave room for the file-name line and model prefix within the embedding model's input limit.
    max_tokens = min(s.chunk_tokens, get_spec(s.embedding_model).max_tokens - 64)
    drafts: list[ChunkDraft] = []
    for seg in segments:
        label = loc_label(seg.loc_type, page=seg.page, line_start=seg.line_start, line_end=seg.line_end,
                          heading=seg.heading)
        header = source_header(file_name, label, section_of(seg.loc_type, seg.heading))
        for text, n in chunk_segment_texts(seg.text, max_tokens, s.chunk_overlap_tokens):
            if not has_content(text, seg.heading if seg.loc_type == "page" else None):
                continue
            drafts.append(ChunkDraft(ordinal=len(drafts), text=text, token_count=n, segment=seg, label=label,
                                     header=header))
    return drafts
