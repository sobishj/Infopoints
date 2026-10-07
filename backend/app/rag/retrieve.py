"""Hybrid retrieval: pgvector cosine (HNSW) + Postgres full-text, merged with reciprocal rank fusion.
Always restricted to the caller's allowed project ids."""
import re
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session
from pgvector.sqlalchemy import Vector

from app.config import get_settings
from app.embed.models import get_spec
from app.locations import loc_label, section_of

RRF_K = 60
# Section matching: boost passages of the section whose title clearly matches the question.
SECTION_WEIGHT = 2.0            # the clearest intent signal in structured documents (API references, manuals)
SECTION_DISTINCT_MARGIN = 0.015 # best section must beat the 5th best by this much (measured 0.025-0.08 for real
                                # matches, 0.004 for a question no section is about)
SECTION_TIE_MARGIN = 0.005      # sections this close to the best are boosted too
SECTION_CANDIDATES = 8
# "What is KonfiPay?" is answered by a document's overview, which keyword search can't single out (the name is
# on every page). For such questions the overview/introduction sections are searched as an extra list.
OVERVIEW_WEIGHT = 1.5
OVERVIEW_CANDIDATES = 3
_DEFINITIONAL = re.compile(r"^\s*(?:what\s+(?:is|are)\b|what's\b|tell\s+me\s+about\b|describe\b|explain\b|"
                           r"(?:give\s+(?:me\s+)?)?an?\s+(?:overview|summary|introduction)\b|"
                           r"(?:overview|summary|introduction)\s+of\b|what\s+does\s+.+\s+(?:do|cover|offer|provide)\b)",
                           re.I)
_NOT_DEFINITIONAL = re.compile(r"\b(?:code|sample|example|snippet|steps?|how|parameters?|error|response)\b", re.I)
KEYWORD_COVERAGE = 0.75   # share of the question's search terms a passage must contain to count as a keyword match


@dataclass
class Retrieved:
    chunk_id: int
    file_id: int
    file_name: str
    kind: str
    ext: str
    loc_type: str
    page: int | None
    t_start: float | None
    t_end: float | None
    label: str
    section: str | None
    header: str
    text: str
    similarity: float | None
    fts_rank: int | None
    score: float
    file_sha256: str | None


def _vector_hits(db: Session, qvec: list[float], projects: list[int], k: int) -> list[tuple[int, float]]:
    db.execute(text("SET LOCAL hnsw.ef_search = 100"))
    try:  # pgvector >= 0.8: keep scanning the graph until enough rows pass the project filter
        db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    except Exception:
        db.rollback()
        db.execute(text("SET LOCAL hnsw.ef_search = 200"))
    rows = db.execute(
        text("""SELECT c.id, 1 - (c.embedding <=> :q) AS sim FROM chunks c
                WHERE c.project_id = ANY(:p)
                ORDER BY c.embedding <=> :q LIMIT :k""").bindparams(bindparam("q", type_=Vector())),
        {"q": qvec, "p": projects, "k": k},
    ).all()
    return [(r[0], float(r[1])) for r in rows]


def _fts_hits(db: Session, question: str, projects: list[int], k: int) -> list[tuple[int, float]]:
    """Keyword matches ranked by how many of the question's words they contain (any word may match).

    Returns [(chunk id, coverage)] where coverage is the share of the question's search terms found."""
    rows = db.execute(
        text("""
            WITH q AS (
                SELECT tsvector_to_array(to_tsvector('english', ip_search_text(:q))) AS terms,
                       replace(plainto_tsquery('english', ip_search_text(:q))::text, '&', '|')::tsquery AS query)
            SELECT c.id,
                   (SELECT count(*) FROM unnest(q.terms) t WHERE c.tsv @@ quote_literal(t)::tsquery)::float
                       / greatest(cardinality(q.terms), 1) AS coverage
            FROM chunks c, q
            WHERE c.project_id = ANY(:p) AND cardinality(q.terms) > 0 AND c.tsv @@ q.query
            ORDER BY coverage DESC, ts_rank_cd(c.tsv, q.query) DESC
            LIMIT :k"""),
        {"q": question, "p": projects, "k": k},
    ).all()
    return [(r[0], float(r[1])) for r in rows]


def _section_hits(db: Session, qvec: list[float], projects: list[int], k: int) -> list[tuple[int, float]]:
    """Passages from the section whose title clearly matches the question ("get an access token" →
    "3 Authentication › 3.1 RequestToken"), best passages first.

    Only used when one section stands out from the rest; for documents without meaningful headings all
    section titles score about the same and this returns nothing."""
    q = bindparam("q", type_=Vector())
    top = db.execute(
        text("""SELECT heading, 1 - min(heading_embedding <=> :q) AS sim
                FROM chunks WHERE project_id = ANY(:p) AND heading_embedding IS NOT NULL
                GROUP BY heading ORDER BY sim DESC LIMIT 5""").bindparams(q),
        {"q": qvec, "p": projects}).all()
    if len(top) < 5 or top[0].sim - top[4].sim < SECTION_DISTINCT_MARGIN:
        return []
    chosen = [r.heading for r in top if top[0].sim - r.sim <= SECTION_TIE_MARGIN]
    rows = db.execute(
        text("""SELECT id, 1 - (embedding <=> :q) AS sim FROM chunks
                WHERE project_id = ANY(:p) AND heading = ANY(:h)
                ORDER BY embedding <=> :q LIMIT :k""").bindparams(q),
        {"q": qvec, "p": projects, "h": chosen, "k": k}).all()
    return [(r.id, float(r.sim)) for r in rows]

def is_definitional(question: str) -> bool:
    return bool(_DEFINITIONAL.search(question)) and not _NOT_DEFINITIONAL.search(question)         and len(question.split()) <= 10


def _overview_hits(db: Session, qvec: list[float], projects: list[int], k: int) -> list[tuple[int, float]]:
    q = bindparam("q", type_=Vector())
    rows = db.execute(
        text(r"""SELECT id, 1 - (embedding <=> :q) AS sim FROM chunks
                 WHERE project_id = ANY(:p)
                   AND heading ~* '\m(overview|introduction|summary|purpose|background|about)\M'
                 ORDER BY embedding <=> :q LIMIT :k""").bindparams(q),
        {"q": qvec, "p": projects, "k": k}).all()
    return [(r.id, float(r.sim)) for r in rows]


def rrf(*ranked: tuple[list[tuple[int, float]], float]) -> list[tuple[int, float]]:
    """Weighted reciprocal rank fusion over (hits, weight) lists."""
    scores: dict[int, float] = {}
    for hits, weight in ranked:
        for rank, (cid, _) in enumerate(hits, 1):
            scores[cid] = scores.get(cid, 0) + weight / (RRF_K + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def retrieve(db: Session, question: str, qvec: list[float], projects: list[int],
             top_k: int | None = None) -> tuple[list[Retrieved], bool]:
    """Return (top chunks, relevant?). `relevant` is the gate that decides whether to ask the LLM at all."""
    s = get_settings()
    if not projects:
        return [], False
    vec = _vector_hits(db, qvec, projects, s.retrieval_candidates)
    fts = _fts_hits(db, question, projects, s.retrieval_candidates)
    sections = _section_hits(db, qvec, projects, SECTION_CANDIDATES)
    overview = _overview_hits(db, qvec, projects, OVERVIEW_CANDIDATES) if is_definitional(question) else []
    fused = rrf((vec, 1.0), (fts, 1.0), (sections, SECTION_WEIGHT), (overview, OVERVIEW_WEIGHT))[
        : top_k or s.retrieval_top_k]
    if not fused:
        return [], False
    sim = dict(vec)
    fts_rank = {cid: i for i, (cid, _) in enumerate(fts, 1)}
    ids = [cid for cid, _ in fused]
    rows = {r.id: r for r in db.execute(
        text("""SELECT c.id, c.file_id, f.file_name, f.kind, f.ext, f.sha256, c.loc_type, c.page, c.t_start, c.t_end,
                       c.line_start, c.line_end, c.heading, c.sheet, c.row_start, c.row_end, c.source_header, c.text
                FROM chunks c JOIN files f ON f.id = c.file_id
                WHERE c.id = ANY(:ids) AND f.status = 'indexed'"""), {"ids": ids}).all()}
    out = []
    for cid, score in fused:
        r = rows.get(cid)
        if r is None:
            continue
        out.append(Retrieved(
            chunk_id=r.id, file_id=r.file_id, file_name=r.file_name, kind=r.kind, ext=r.ext, loc_type=r.loc_type,
            page=r.page, t_start=r.t_start, t_end=r.t_end,
            label=loc_label(r.loc_type, page=r.page, line_start=r.line_start, line_end=r.line_end, heading=r.heading,
                            sheet=r.sheet, row_start=r.row_start, row_end=r.row_end, t_start=r.t_start,
                            t_end=r.t_end),
            section=section_of(r.loc_type, r.heading), header=r.source_header, text=r.text,
            similarity=sim.get(cid), fts_rank=fts_rank.get(cid), score=score, file_sha256=r.sha256))
    return out, is_relevant(vec, fts)


def is_relevant(vec: list[tuple[int, float]], fts: list[tuple[int, float]]) -> bool:
    """The gate in front of the LLM: is anything in the index actually about this question?

    Yes if the best meaning match clears the model's calibrated threshold, or a passage contains most of the
    question's search terms while the meaning is not far off."""
    s = get_settings()
    threshold = s.min_vector_similarity or get_spec(s.embedding_model).min_similarity
    best_sim = max((sim for _, sim in vec), default=0.0)
    best_coverage = max((cov for _, cov in fts), default=0.0)
    return best_sim >= threshold or (best_coverage >= KEYWORD_COVERAGE and best_sim >= threshold - 0.08)
