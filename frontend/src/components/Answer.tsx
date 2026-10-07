import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FileText, FileType2, Presentation, ExternalLink, AlertCircle } from "lucide-react";

import type { SourceCard as Card, Turn } from "../api/types";
import { linkCitations } from "../format";

interface HoverProps {
  active: number | null;
  onHover: (n: number | null) => void;
}

export function CitationChip({ n, active, onHover }: { n: number } & HoverProps) {
  return (
    <a href={`#source-${n}`}
       onMouseEnter={() => onHover(n)} onMouseLeave={() => onHover(null)}
       onFocus={() => onHover(n)} onBlur={() => onHover(null)}
       onClick={(e) => { e.preventDefault(); document.getElementById(`source-${n}`)?.scrollIntoView({ block: "nearest" }); }}
       aria-label={`Source ${n}`}
       className={`mx-0.5 inline-flex h-[1.35em] min-w-[1.35em] items-center justify-center rounded px-1 align-[0.1em]
                   text-[0.78em] font-semibold no-underline transition-colors
                   ${active === n ? "bg-accent text-white" : "bg-accent-soft text-accent"}`}>
      {n}
    </a>
  );
}

export function AnswerBody({ turn, active, onHover }: { turn: Turn } & HoverProps) {
  if (turn.status === "error") {
    return (
      <div className="flex gap-2 rounded-md border border-line bg-surface p-3 text-[15px]" role="alert">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden />
        <span>{turn.error}</span>
      </div>
    );
  }
  if (turn.status === "not_found") {
    return (
      <div className="rounded-md border border-line bg-surface p-4">
        <p className="font-semibold">Not found in documents</p>
        <p className="mt-1 text-muted">I could not find anything about this in the selected document folders.
          {turn.sources.length > 0 ? " These sources look related and may help:" : " Try rephrasing, or check that the right project is selected."}
        </p>
      </div>
    );
  }
  if (turn.status === "streaming" && !turn.text) {
    return <Thinking turn={turn} />;
  }
  return (
    <div className="prose-answer" aria-live={turn.status === "streaming" ? "polite" : undefined}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#cite-")) {
              return <CitationChip n={Number(href.slice(6))} active={active} onHover={onHover} />;
            }
            return <a href={href} className="link">{children}</a>;
          },
        }}>
        {linkCitations(turn.text) + (turn.status === "streaming" ? " ▍" : "")}
      </ReactMarkdown>
      {turn.uncited && (
        <p className="mt-2 text-[14px] text-muted">This answer has no source references — check it against the documents before relying on it.</p>
      )}
    </div>
  );
}

/** Shown until the first words of the answer arrive. */
export function Thinking({ turn }: { turn: Turn }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = turn.startedAt ?? Date.now();
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, [turn.startedAt]);

  const n = turn.sources.length;
  const label = turn.phase === "reading"
    ? `Reading ${n} source${n === 1 ? "" : "s"} and writing the answer`
    : "Searching your documents";
  return (
    <div className="flex flex-col gap-1.5 py-1" role="status" aria-live="polite">
      <div className="flex items-center gap-3">
        <span className="thinking-dots" aria-hidden><span /><span /><span /></span>
        <span className="thinking-text text-[15px]">{label}…</span>
      </div>
      {elapsed >= 12 && (
        <p className="pl-[38px] text-[13.5px] text-muted">
          {elapsed}s — the local AI model may be starting up; the first answer after a quiet period can take a minute.
        </p>
      )}
    </div>
  );
}

/** Sources listed after the answer, like references at the end of a document. */
export function References({ turn, active, onHover }: { turn: Turn } & HoverProps) {
  const { title, cards } = visibleSources(turn);
  if (!cards.length) return null;
  return (
    <section className="mt-5 border-t border-line pt-4" aria-label={title}>
      <h3 className="mb-2 text-[12.5px] font-semibold uppercase tracking-wide text-muted">{title}</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        {cards.map((c) => <SourceCardView key={c.n} card={c} active={active} onHover={onHover} />)}
      </div>
    </section>
  );
}

function FileIcon({ card }: { card: Card }) {
  const ext = (card.ext ?? card.file_name.split(".").pop() ?? "").toLowerCase().replace(".", "");
  const Icon = ["ppt", "pptx", "odp"].includes(ext) ? Presentation : ext === "pdf" ? FileType2 : FileText;
  return <Icon className="h-4 w-4 shrink-0 text-muted" aria-hidden />;
}

export function SourceCardView({ card, active, onHover }: { card: Card } & HoverProps) {
  const highlighted = active === card.n;
  return (
    <article id={`source-${card.n}`}
             onMouseEnter={() => onHover(card.n)} onMouseLeave={() => onHover(null)}
             className={`card p-3 transition-colors ${highlighted ? "border-accent bg-accent-soft/40" : ""}`}>
      <div className="flex items-start gap-2">
        <span className={`mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded px-1 text-[12px] font-semibold
                          ${highlighted ? "bg-accent text-white" : "bg-accent-soft text-accent"}`}>{card.n}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <FileIcon card={card} />
            <span className="truncate text-[14.5px] font-semibold" title={card.file_name}>{card.file_name}</span>
          </div>
          <div className="mt-0.5 truncate text-[13.5px] text-muted" title={card.section ?? undefined}>
            {card.label}{card.section ? ` · ${card.section.split(" › ").pop()}` : ""}
          </div>
        </div>
      </div>
      <p className="mt-2 line-clamp-3 text-[13.5px] leading-snug text-muted">{card.snippet}</p>
      <div className="mt-2 flex items-center justify-between">
        {card.source_changed ? <span className="text-[12.5px] text-muted">File changed since this answer</span> : <span />}
        <a href={card.open_url} target="_blank" rel="noreferrer" className="btn-quiet px-2 py-1 text-[13.5px]">
          Open {card.page ? `page ${card.page}` : ""} <ExternalLink className="h-3.5 w-3.5" aria-hidden />
        </a>
      </div>
    </article>
  );
}

/** Cited sources for answered questions; related ones for "not found". */
export function visibleSources(turn: Turn): { title: string; cards: Card[] } {
  if (turn.status === "not_found") return { title: "Related sources", cards: turn.sources };
  if (turn.status === "streaming") return { title: "References", cards: [] };  // answer first, references after
  return { title: "References", cards: turn.sources.filter((s) => s.cited) };
}
