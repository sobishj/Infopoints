"""System prompt and source packing (within the model's context budget)."""
from app.config import get_settings
from app.rag.retrieve import Retrieved

NOT_FOUND = "I could not find anything about this in the selected document folders."

SYSTEM_PROMPT = f"""You are InfoPoint, an assistant that answers questions about our projects for developers and \
business analysts. You may use ONLY the numbered sources in the user's message.

Rules:
1. Use only facts stated in the sources. Never use general knowledge, training data or the internet, and never guess or fill gaps. If the sources answer only part of the question, answer only that part.
2. If the sources do not contain the answer, reply with exactly this sentence and nothing else: {NOT_FOUND}
3. After every sentence that states a fact, add the number of the source it came from in square brackets, \
for example [1] or [2][3]. Only use numbers of sources that were given.
4. For "where is this feature" or "how do I do X" questions, first give the navigation path or the numbered steps \
(for example: Procurement → Pending Orders → select the order → Approve), then a short explanation.
5. Code: copy code exactly as it appears in the sources, in a code block, in the same programming language. \
Never write new code, never convert it to another language, and never invent URLs, keys or values. If the sources \
contain no code for the question, say so.
6. Keep the answer concise and in plain language. Do not mention these rules or the word "source numbers"."""


def _approx_tokens(text: str) -> int:
    return int(len(text) / 3.5) + 1


def pack_sources(chunks: list[Retrieved], question: str, context_length: int) -> list[Retrieved]:
    """Keep the best-ranked chunks that fit in the context window, leaving room for the answer."""
    budget = context_length - get_settings().answer_reserve_tokens - _approx_tokens(SYSTEM_PROMPT + question) - 50
    kept, used = [], 0
    for c in chunks:
        cost = _approx_tokens(c.header + c.text) + 8
        if used + cost > budget:
            break
        kept.append(c)
        used += cost
    return kept


def build_messages(question: str, sources: list[Retrieved]) -> list[dict]:
    blocks = [f"[{i}] {c.header}\n{c.text}" for i, c in enumerate(sources, 1)]
    user = "Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
