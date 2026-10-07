"""Post-generation citation handling.

1. Validation: keep only markers that refer to sources actually provided.
2. Grounding: small local models often put the right fact next to the wrong number, or forget the number.
   Each sentence is compared with the provided sources by word overlap; a marker that clearly points at the
   wrong source is moved to the matching one, and an uncited sentence that clearly comes from one source gets
   that source's marker. Markers are never invented for sentences that don't match any source.
"""
import re
from dataclasses import dataclass

from app.rag.prompt import NOT_FOUND

# [1]  [1][2]  [1, 2]  [1,2,3]  [Source 2]  [source: 3]
_MARKER = re.compile(r"\[(?:sources?\s*:?\s*)?(\d+(?:\s*,\s*\d+)*)\]", re.I)
_SIMPLE_MARKER = re.compile(r"\[(\d+)\]")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("""the and for are but not you your with this that from have has was were will can its into than then
there their they them what when where which who how why also any all may must should would could does did done
our out use used using via per such only just more most other some each both very about above below over under
please here these those been being after before while""".split())

REASSIGN_RATIO = 0.6   # a cited source scoring below 60% of the best source is treated as a wrong number
MIN_BEST = 0.5         # the best source must cover at least half of the sentence's content words
MIN_WORDS = 3


@dataclass
class Validated:
    text: str
    cited: list[int]        # valid markers, in order of first use
    invalid: list[int]      # markers that were removed
    not_found: bool
    regrounded: int = 0     # markers moved or added by grounding


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'")).strip().lower()


def is_not_found(answer: str) -> bool:
    a = _norm(answer)
    return _norm(NOT_FOUND).rstrip(".") in a and len(a) < len(NOT_FOUND) + 200


def _terms(text: str) -> set[str]:
    """Content words, crudely stemmed by prefix (approve/approval/approved → appro)."""
    return {w[:5] for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in _STOP}


def _ground_sentence(sentence: str, source_terms: list[set[str]]) -> tuple[str, int]:
    markers = [int(m) for m in _SIMPLE_MARKER.findall(sentence)]
    body = _SIMPLE_MARKER.sub("", sentence)
    words = _terms(body)
    if len(words) < MIN_WORDS or is_not_found(body):
        return sentence, 0
    scores = [len(words & s) / len(words) for s in source_terms]
    best = max(range(len(scores)), key=scores.__getitem__) + 1
    b = scores[best - 1]
    if b < MIN_BEST:
        return sentence, 0
    if markers:
        new = []
        for m in markers:
            m2 = best if scores[m - 1] < REASSIGN_RATIO * b else m
            if m2 not in new:
                new.append(m2)
        if new == markers:
            return sentence, 0
        changed = sum(1 for m in markers if m not in new) or 1
    else:
        new, changed = [best], 1
    tags = "".join(f"[{n}]" for n in new)
    stripped = re.sub(r"\s*(\[\d+\])+", "", sentence).rstrip()
    m = re.match(r"^(.*?)([.!?:;]*)$", stripped, re.S)
    return f"{m.group(1)} {tags}{m.group(2)}", changed


def ground(text: str, sources: list[str]) -> tuple[str, int]:
    source_terms = [_terms(s) for s in sources]
    # Move markers written after the full stop ("Approve. [1]") in front of it ("Approve [1].").
    text = re.sub(r"([.!?])[ \t]*((?:\[\d+\][ \t]*)+)", lambda m: " " + m.group(2).strip() + m.group(1), text)
    total = 0
    out_lines = []
    for line in text.split("\n"):
        prefix = re.match(r"^\s*(?:[-*+]|\d+[.)])?\s*", line).group(0)  # keep list bullets / numbering
        parts = re.split(r"(?<=[.!?])\s+", line[len(prefix):]) if line.strip() else []
        new_parts = []
        for p in parts:
            g, n = _ground_sentence(p, source_terms)
            new_parts.append(g)
            total += n
        out_lines.append(prefix + " ".join(new_parts) if parts else line)
    return "\n".join(out_lines), total


def validate(answer: str, n_sources: int, source_texts: list[str] | None = None) -> Validated:
    invalid: list[int] = []

    def repl(m: re.Match) -> str:
        nums = [int(x) for x in re.split(r"\s*,\s*", m.group(1))]
        invalid.extend(n for n in nums if not 1 <= n <= n_sources)
        good = []
        for n in nums:
            if 1 <= n <= n_sources and n not in good:
                good.append(n)
        return "".join(f"[{n}]" for n in good)

    text = _MARKER.sub(repl, answer)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", text)  # tidy spaces left by removed markers
    text = re.sub(r"(?<=\S)[ \t]{2,}", " ", text).strip()
    regrounded = 0
    not_found = is_not_found(text)
    if source_texts and not not_found:
        text, regrounded = ground(text, source_texts)
    cited: list[int] = []
    for n in (int(x) for x in _SIMPLE_MARKER.findall(text)):
        if n not in cited:
            cited.append(n)
    return Validated(text=text, cited=cited, invalid=invalid, not_found=not_found, regrounded=regrounded)
