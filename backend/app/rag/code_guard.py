"""Code samples in answers must come from the documents.

Small models happily write plausible code in a different language when asked for "sample code", or answer a
code question with a fragment. So:
  * every fenced code block is checked against the provided sources line by line: each line must occur in
    them word for word (ignoring spacing). Otherwise the block is replaced by the closest real code passage,
    quoted verbatim and cited;
  * when the question asks for code and the answer has no real sample, the documents' sample is added;
  * a sample is shown once, cut where the documents' sample ends, and preceded by the method's definition
    (e.g. "ChangeMailAddressUser(...) : Boolean") when the sources contain it.
"""
import re

_FENCE = re.compile(r"```[^\n]*\n(.*?)(?:```|\Z)", re.S)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CODE_LINE = re.compile(r"[=(){}\[\];]|^\s*(IF|ELSE|END|FOR|RETURN|Step \d)\b", re.I)
_STEP_START = re.compile(r"^\s*(Sample (API|Method|WinDev API) Call|Step 1\b)", re.I | re.M)
# Headings that follow a sample in the reference documents: the sample ends there.
_SAMPLE_END = re.compile(r"^\s*(Successful Response|Error Handling|Failure Response|Error Response|Notes|"
                         r"Request URL Example|ErrorItem Definition|Processing Flow|Example Output|Meaning)\s*$",
                         re.I | re.M)
_CALLED = re.compile(r"\.(\w+)\(")

MAX_EXCERPT_LINES = 40
SAMPLE_BONUS = 30       # a passage with a worked example beats a method definition
MIN_STATEMENTS = 2     # assignments/calls a block needs to count as a sample (JSON data alone doesn't)
_STATEMENT = re.compile(r"[A-Za-z_][\w.]*\s*(=(?!=)|\()")
_ASKS_FOR_CODE = re.compile(r"\b(code|sample|example|snippet|syntax|how (do|to) (i )?call|usage)\b", re.I)


def _idents(text: str) -> set[str]:
    return {w.lower() for w in _IDENT.findall(text)}


def _norm_code(text: str) -> str:
    text = text.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-"}))
    return re.sub(r"\s+", " ", re.sub(r"\[\d+\]", "", text)).strip()


def _verbatim(block: str, source_text: str) -> bool:
    """Every line of the block occurs word for word in the sources (spacing ignored)."""
    return all(_norm_code(ln) in source_text for ln in block.splitlines() if _norm_code(ln))


def _lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


def _code_score(text: str) -> int:
    return sum(1 for ln in text.splitlines() if _CODE_LINE.search(ln))


def _is_program(block: str) -> bool:
    return sum(1 for ln in block.splitlines() if _STATEMENT.search(ln)) >= MIN_STATEMENTS


def _best_source(sources: list[str], wanted: set[str]) -> int | None:
    """Index of the source most likely to hold the relevant sample: a worked example ("Sample API Call",
    "Step 1") first, then code-like passages matching the question's identifiers, ranked early by retrieval
    (sources are in rank order)."""
    scored = [(SAMPLE_BONUS * bool(_STEP_START.search(s)) + len(wanted & _idents(s)) + 2 * min(_code_score(s), 10)
               - rank, rank)
              for rank, s in enumerate(sources) if _code_score(s) >= 3]
    return max(scored)[1] if scored else None


def _strip_page_number(text: str) -> str:
    return re.sub(r"^\s*\d{1,4}\s*\n", "", text, count=1)  # the page number PDF text starts with


def _sample(sources: list[str], i: int, next_of: dict[int, int]) -> tuple[str, list[int]]:
    """The sample starting in source i ('Sample API Call' / 'Step 1' onwards), continued into the next page's
    passage when that is also a source, and cut at the heading that follows the sample."""
    m = _STEP_START.search(sources[i])
    text, markers = sources[i][m.start() if m else 0:], [i]
    j = next_of.get(i)
    while j is not None and j not in markers and not _SAMPLE_END.search(text, 1):
        text += "\n" + _strip_page_number(sources[j])
        markers.append(j)
        j = next_of.get(j)
    end = _SAMPLE_END.search(text, 1)
    if end:
        text = text[:end.start()]
        markers = [k for k in markers if k == i or _norm_code(_strip_page_number(sources[k]))[:40] in _norm_code(text)]
    return "\n".join(_lines(text)[:MAX_EXCERPT_LINES * 2]), markers


def _definition(sources: list[str], sample: str) -> tuple[int, str] | None:
    """The definition of the method the sample calls, e.g. 'ChangeMailAddressUser(\\n inRID, ...\\n) : Boolean'."""
    for name in dict.fromkeys(_CALLED.findall(sample)):
        pattern = re.compile(rf"^[ \t]*(?:PROCEDURE[ \t]+)?{re.escape(name)}\([^()]*\)[ \t]*:[ \t]*\w+", re.M)
        for k, s in enumerate(sources):
            m = pattern.search(s)
            if m:
                return k, "\n".join(_lines(m.group(0)))
    return None


def _sample_block(sources: list[str], i: int, next_of: dict[int, int]) -> str:
    sample, markers = _sample(sources, i, next_of)
    out = ""
    d = _definition(sources, sample)
    if d and _norm_code(d[1]) not in _norm_code(sample):
        out = f"The documents define the method as [{d[0] + 1}]:\n\n```text\n{d[1]}\n```\n\n"
    cites = "".join(f"[{m + 1}]" for m in markers)
    return out + f"The documents show this sample {cites}:\n\n```text\n{sample}\n```"


def guard_code(answer: str, sources: list[str], question: str,
               next_of: dict[int, int] | None = None) -> tuple[str, int]:
    """Return (answer with invented code replaced and, for code questions, the real sample added;
    number of blocks replaced or added). `next_of` maps a source index to the source that continues it on
    the next page (same file and section)."""
    next_of = next_of or {}
    source_text = _norm_code("\n".join(sources))
    changes = 0
    real_samples = 0
    shown: set[int] = set()  # sources whose sample is already in the answer

    def check(m: re.Match) -> str:
        nonlocal changes, real_samples
        block = m.group(1)
        if _verbatim(block, source_text):
            if _is_program(block):
                real_samples += 1
            return m.group(0)
        changes += 1
        i = _best_source(sources, _idents(block) | _idents(question))
        if i is None:
            return "_The documents don't include a code sample for this._"
        real_samples += 1
        if i in shown:
            return f"_See the sample from the documents above [{i + 1}]._"
        shown.add(i)
        return _sample_block(sources, i, next_of)

    text = _FENCE.sub(check, answer)
    if real_samples == 0 and _ASKS_FOR_CODE.search(question):
        i = _best_source(sources, _idents(question) | _idents(text))
        if i is not None:
            text = text.rstrip() + "\n\n" + _sample_block(sources, i, next_of)
            changes += 1
    return text, changes
