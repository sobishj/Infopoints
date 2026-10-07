"""Hybrid retrieval: pgvector cosine (HNSW) + Postgres full-text, merged with reciprocal rank fusion.
Always restricted to the caller's allowed project ids."""
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session
from pgvector.sqlalchemy import Vector

from app.config import get_settings
from app.locations import loc_label

RRF_K = 60


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
                ORDER BY c.embedding <=> :q LIMIT :k""").bindparams(bindparam("q", type_=Vector(1024))),
        {"q": qvec, "p": projects, "k": k},
    ).all()
    return [(r[0], float(r[1])) for r in rows]


def _fts_hits(db: Session, question: str, projects: list[int], k: int) -> list[int]:
    rows = db.execute(
        text("""SELECT c.id FROM chunks c, websearch_to_tsquery('english', :q) query
                WHERE c.project_id = ANY(:p) AND c.tsv @@ query
                ORDER BY ts_rank_cd(c.tsv, query) DESC LIMIT :k"""),
        {"q": question, "p": projects, "k": k},
    ).all()
    return [r[0] for r in rows]


def rrf(vector: list[tuple[int, float]], fts: list[int]) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for rank, (cid, _) in enumerate(vector, 1):
        scores[cid] = scores.get(cid, 0) + 1 / (RRF_K + rank)
    for rank, cid in enumerate(fts, 1):
        scores[cid] = scores.get(cid, 0) + 1 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def retrieve(db: Session, question: str, qvec: list[float], projects: list[int],
             top_k: int | None = None) -> tuple[list[Retrieved], bool]:
    """Return (top chunks, relevant?). `relevant` is the gate that decides whether to ask the LLM at all."""
    s = get_settings()
    if not projects:
        return [], False
    vec = _vector_hits(db, qvec, projects, s.retrieval_candidates)
    fts = _fts_hits(db, question, projects, s.retrieval_candidates)
    fused = rrf(vec, fts)[: top_k or s.retrieval_top_k]
    if not fused:
        return [], False
    sim = dict(vec)
    fts_rank = {cid: i for i, cid in enumerate(fts, 1)}
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
            header=r.source_header, text=r.text, similarity=sim.get(cid), fts_rank=fts_rank.get(cid), score=score,
            file_sha256=r.sha256))
    best_sim = max((x.similarity or 0 for x in out), default=0)
    relevant = best_sim >= s.min_vector_similarity or any(x.fts_rank == 1 for x in out) and best_sim >= 0.3
    return out, relevant
