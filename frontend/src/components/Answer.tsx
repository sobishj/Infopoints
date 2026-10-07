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
        <p className="mt-1 text-muted">I couldn't find this in the project documents.
          {turn.sources.length > 0 ? " These sources look related and may help:" : " Try rephrasing, or check that the right project is selected."}
        </p>
      </div>
    );
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
          <div className="mt-0.5 text-[13.5px] text-muted">{card.label}</div>
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
  if (turn.status === "streaming") return { title: "Searching sources", cards: turn.sources };
  const cited = turn.sources.filter((s) => s.cited);
  return { title: "Sources", cards: cited };
}
