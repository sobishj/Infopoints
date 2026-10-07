"""Post-generation citation handling.

1. Validation: keep only markers that refer to sources actually provided.
2. Grounding: small local models often put the right fact next to the wrong number, or forget the number.
   Each sentence is compared with the provided sources by word overlap; a marker that clearly points at the
   wrong source is moved to the matching one, and an uncited sentence that clearly comes from one source gets
   that source's marker. Markers are never invented for sentences that don't match any source.
3. Support check: answers must come from the folders, not from the model's general knowledge. If most of the
   answer's prose matches no source, or the model says the documents don't cover it and answers anyway, the
   whole answer is replaced by the not-found message.
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
MIN_SUPPORTED = 0.5    # share of the answer's prose (by content words) that must match a source
# The model admitting the documents don't cover the question, usually followed by an answer from memory.
_HEDGE = re.compile(r"\b(?:(?:the\s+)?(?:provided\s+|given\s+)?(?:documents?|sources?|context|texts?)\s+"
                    r"(?:do(?:es)?\s*n[o']t|do(?:es)?\s+not)\s+(?:contain|mention|provide|include|specify|"
                    r"cover|describe|explain|say)|not\s+(?:mentioned|provided|covered|specified|described)\s+in\s+"
                    r"the\s+(?:documents?|sources?|context)|(?:based\s+on|from)\s+(?:general|common)\s+knowledge|"
                    r"generally\s+speaking|in\s+general,)", re.I)
# Words that frame a question rather than name its topic.
_QUESTION_WORDS = set("""what how why when where which who does need want show give tell explain example examples
sample samples code way ways step steps work works mean means difference between used make get set can""".split())
_OWN_LINES = re.compile(r"^\s*_?(?:The documents show this sample|The documents define the method|"
                        r"The documents don't include a code sample|See the sample from the documents)", re.I)


@dataclass
class Validated:
    text: str
    cited: list[int]        # valid markers, in order of first use
    invalid: list[int]      # markers that were removed
    not_found: bool
    regrounded: int = 0     # markers moved or added by grounding
    supported: float = 1.0  # share of the prose that matches a source


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


def _strip_markers_in_code(text: str) -> str:
    return re.sub(r"```.*?(?:```|$)", lambda m: re.sub(r"[ \t]*\[\d+\]", "", m.group(0)), text, flags=re.S)


def ground(text: str, sources: list[str]) -> tuple[str, int]:
    source_terms = [_terms(s) for s in sources]
    # Move markers written after the full stop ("Approve. [1]") in front of it ("Approve [1]."),
    # and take markers out of code blocks, where they would corrupt the sample.
    text = _strip_markers_in_code(text)
    text = re.sub(r"([.!?])[ \t]*((?:\[\d+\][ \t]*)+)", lambda m: " " + m.group(2).strip() + m.group(1), text)
    total = 0
    out_lines = []
    in_code = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        # Never touch code samples, nor the lines InfoPoint wrote itself (their markers are already exact).
        if in_code or line.lstrip().startswith("```") or _OWN_LINES.match(line):
            out_lines.append(line)
            continue
        prefix = re.match(r"^\s*(?:[-*+]|\d+[.)])?\s*", line).group(0)  # keep list bullets / numbering
        parts = re.split(r"(?<=[.!?])\s+", line[len(prefix):]) if line.strip() else []
        new_parts = []
        for p in parts:
            g, n = _ground_sentence(p, source_terms)
            new_parts.append(g)
            total += n
        out_lines.append(prefix + " ".join(new_parts) if parts else line)
    return "\n".join(out_lines), total


def _prose(text: str) -> list[str]:
    """Sentences outside code blocks, minus the lines InfoPoint adds itself."""
    text = re.sub(r"```.*?(?:```|$)", "\n", text, flags=re.S)
    out = []
    for line in text.split("\n"):
        if _OWN_LINES.match(line):
            continue
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])?\s*", "", line)
        out.extend(p for p in re.split(r"(?<=[.!?])\s+", line) if p.strip())
    return out


def support(text: str, sources: list[str]) -> tuple[float, bool]:
    """(share of the prose's content words in sentences that match a source, model hedged about the documents)."""
    source_terms = [_terms(s) for s in sources]
    total = backed = 0
    for sentence in _prose(text):
        words = _terms(_SIMPLE_MARKER.sub("", sentence))
        if len(words) < MIN_WORDS:
            continue
        total += len(words)
        if source_terms and max(len(words & s) / len(words) for s in source_terms) >= MIN_BEST:
            backed += len(words)
    hedged = any(_HEDGE.search(s) for s in _prose(text))
    return (backed / total if total else 1.0), hedged


def off_topic_terms(question: str, answer: str, sources: list[str]) -> set[str]:
    """Question topic words that no source contains but the answer talks about anyway
    ("refresh token" when the documents never mention refreshing): the answer came from somewhere else."""
    in_sources = set().union(*(_terms(s) for s in sources)) if sources else set()
    topic = {w[:5] for w in _WORD.findall(question.lower())
             if len(w) >= 4 and w not in _STOP and w not in _QUESTION_WORDS}
    prose = _terms(" ".join(_prose(answer)))
    return {t for t in topic - in_sources if t in prose}


def validate(answer: str, n_sources: int, source_texts: list[str] | None = None,
             question: str | None = None) -> Validated:
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
    supported = 1.0
    if source_texts and not not_found:
        supported, hedged = support(text, source_texts)
        off_topic = off_topic_terms(question, text, source_texts) if question else set()
        if hedged or supported < MIN_SUPPORTED or off_topic:
            text, not_found = NOT_FOUND, True
    if source_texts and not not_found:
        text, regrounded = ground(text, source_texts)
    cited: list[int] = []
    for n in (int(x) for x in _SIMPLE_MARKER.findall(text)):
        if n not in cited:
            cited.append(n)
    return Validated(text=text, cited=cited, invalid=invalid, not_found=not_found, regrounded=regrounded,
                     supported=supported)
