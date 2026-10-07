import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Plus, ArrowUp, MoreHorizontal, Trash2 } from "lucide-react";

import { api, postStream } from "../api/client";
import type { Conversation, Project, SourceCard, Turn } from "../api/types";
import { AnswerBody, References } from "../components/Answer";
import { Confirm } from "../components/Modal";

const EXAMPLES = [
  "How do I approve a pending purchase order?",
  "Where can I change a user's access rights?",
  "What was demonstrated in the last sprint review?",
];

export default function AskPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<number[] | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [convId, setConvId] = useState<number | null>(conversationId ? Number(conversationId) : null);
  const [question, setQuestion] = useState("");
  const [active, setActive] = useState<number | null>(null);
  const [focusTurn, setFocusTurn] = useState<number>(-1);
  const [menuFor, setMenuFor] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const busy = turns.some((t) => t.status === "streaming");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const loadConversations = useCallback(async () => {
    setConversations((await api<{ conversations: Conversation[] }>("/api/conversations")).conversations);
  }, []);

  useEffect(() => {
    api<{ projects: Project[] }>("/api/projects").then((r) => {
      setProjects(r.projects);
      setSelected((s) => s ?? r.projects.map((p) => p.id));
    });
    loadConversations();
  }, [loadConversations]);

  // Load an existing conversation from the URL (unless it's the one we're streaming into).
  useEffect(() => {
    if (!conversationId) {
      if (!busy) { setTurns([]); setConvId(null); }
      return;
    }
    const id = Number(conversationId);
    if (id === convId && turns.length) return;
    api<{ messages: { role: string; content: string; status: string; sources: SourceCard[] }[] }>(
      `/api/conversations/${id}`,
    ).then((c) => {
      const loaded: Turn[] = [];
      for (const m of c.messages) {
        if (m.role === "user") loaded.push({ key: `${id}-${loaded.length}`, question: m.content, text: "", status: "ok", sources: [], cited: [] });
        else if (loaded.length) {
          const t = loaded[loaded.length - 1];
          t.text = m.content;
          t.status = m.status === "not_found" ? "not_found" : m.status === "error" ? "error" : "ok";
          t.error = m.status === "error" ? m.content : undefined;
          t.sources = m.sources;
          t.cited = m.sources.filter((s) => s.cited).map((s) => s.n);
        }
      }
      setTurns(loaded);
      setConvId(id);
      setFocusTurn(-1);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  const update = (key: string, fn: (t: Turn) => Turn) =>
    setTurns((ts) => ts.map((t) => (t.key === key ? fn(t) : t)));

  const ask = async (q: string) => {
    q = q.trim();
    if (!q || busy) return;
    const key = `${Date.now()}`;
    setTurns((ts) => [...ts, { key, question: q, text: "", status: "streaming", phase: "searching", startedAt: Date.now(),
                                 sources: [], cited: [] }]);
    setFocusTurn(-1);
    setQuestion("");
    try {
      await postStream("/api/ask", { question: q, project_ids: selected, conversation_id: convId }, (event, data) => {
        if (event === "meta") {
          setConvId(data.conversation_id);
          if (String(data.conversation_id) !== conversationId) navigate(`/c/${data.conversation_id}`, { replace: true });
        } else if (event === "sources") update(key, (t) => ({ ...t, sources: data.sources, phase: "reading" }));
        else if (event === "token") update(key, (t) => ({ ...t, text: t.text + data.t, phase: "writing" }));
        else if (event === "final")
          update(key, (t) => ({ ...t, text: data.text, status: data.status, cited: data.cited, uncited: data.uncited,
                                sources: data.sources ?? t.sources }));
        else if (event === "error") update(key, (t) => ({ ...t, status: "error", error: data.message }));
      });
    } catch (e) {
      update(key, (t) => ({ ...t, status: "error", error: (e as Error).message }));
    }
    update(key, (t) => (t.status === "streaming" ? { ...t, status: "ok" } : t));
    loadConversations();
    inputRef.current?.focus();
  };

  const submit = (e: FormEvent) => { e.preventDefault(); ask(question); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(question); }
  };

  const newConversation = () => { setTurns([]); setConvId(null); navigate("/"); inputRef.current?.focus(); };
  const deleteConversation = async (c: Conversation) => {
    setDeleting(null);
    await api(`/api/conversations/${c.id}`, { method: "DELETE" });
    if (c.id === convId) newConversation();
    loadConversations();
  };

  // Close the ⋯ menu on any click outside it.
  useEffect(() => {
    if (menuFor === null) return;
    const close = () => setMenuFor(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menuFor]);

  const toggleProject = (id: number) =>
    setSelected((s) => (s ?? []).includes(id) ? (s ?? []).filter((x) => x !== id) : [...(s ?? []), id]);

  const activeTurn = turns.length ? turns[focusTurn >= 0 ? focusTurn : turns.length - 1] : null;

  return (
    <div className="flex h-full">
      {/* ---------------------------------------------------------------- left sidebar */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-line bg-surface md:flex">
        <div className="p-4">
          <button className="btn-secondary w-full justify-center" onClick={newConversation}>
            <Plus className="h-4 w-4" aria-hidden /> New chat
          </button>
        </div>
        <section className="px-4 pb-4">
          <h2 className="mb-2 text-[12.5px] font-semibold uppercase tracking-wide text-muted">Projects</h2>
          {projects.length === 0 && <p className="text-[14px] text-muted">No projects yet.</p>}
          <ul className="space-y-1">
            {projects.map((p) => (
              <li key={p.id}>
                <label className="flex cursor-pointer items-center gap-2 text-[14.5px]">
                  <input type="checkbox" className="accent-accent" checked={(selected ?? []).includes(p.id)}
                         onChange={() => toggleProject(p.id)} />
                  <span className="truncate">{p.name}</span>
                  <span className="ml-auto text-[12.5px] text-muted">{p.indexed}</span>
                </label>
              </li>
            ))}
          </ul>
        </section>
        <section className="min-h-0 flex-1 overflow-y-auto border-t border-line px-4 py-4">
          <h2 className="mb-2 text-[12.5px] font-semibold uppercase tracking-wide text-muted">Chats</h2>
          {conversations.length === 0 && <p className="text-[14px] text-muted">No chats yet.</p>}
          <ul className="space-y-0.5">
            {conversations.map((c) => (
              <li key={c.id} className={`group relative flex items-center rounded ${c.id === convId ? "bg-accent-soft" : "hover:bg-page"}`}>
                <Link to={`/c/${c.id}`} title={c.title}
                      className={`min-w-0 flex-1 truncate px-2 py-1 text-[14px] ${c.id === convId ? "text-accent" : "text-ink"}`}>
                  {c.title}
                </Link>
                <button type="button" aria-label={`Options for ${c.title}`} aria-haspopup="menu"
                        aria-expanded={menuFor === c.id}
                        onClick={(e) => { e.stopPropagation(); setMenuFor(menuFor === c.id ? null : c.id); }}
                        className={`mr-1 rounded p-1 text-muted hover:bg-line hover:text-ink focus:opacity-100 ${menuFor === c.id || c.id === convId ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                  <MoreHorizontal className="h-4 w-4" aria-hidden />
                </button>
                {menuFor === c.id && (
                  <div role="menu" className="card absolute right-1 top-8 z-20 w-36 py-1 shadow-md"
                       onClick={(e) => e.stopPropagation()}>
                    <button role="menuitem" type="button"
                            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[14px] text-danger hover:bg-page"
                            onClick={() => { setMenuFor(null); setDeleting(c); }}>
                      <Trash2 className="h-4 w-4" aria-hidden /> Delete
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      </aside>

      {/* ---------------------------------------------------------------- reading column */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[720px] px-6 py-8">
            {turns.length === 0 ? (
              <EmptyState onPick={ask} hasProjects={projects.length > 0} />
            ) : (
              turns.map((t, i) => (
                <article key={t.key} className="mb-10" onMouseEnter={() => setFocusTurn(i)}>
                  <h2 className="mb-3 text-[18px] leading-snug">{t.question}</h2>
                  <AnswerBody turn={t} active={activeTurn === t ? active : null} onHover={(n) => { setFocusTurn(i); setActive(n); }} />
                  {t.status !== "streaming" && (
                    <References turn={t} active={activeTurn === t ? active : null}
                                onHover={(n) => { setFocusTurn(i); setActive(n); }} />
                  )}
                </article>
              ))
            )}
            <div ref={bottomRef} />
          </div>
        </div>
        <form onSubmit={submit} className="border-t border-line bg-page">
          <div className="mx-auto flex max-w-[720px] items-end gap-2 px-6 py-4">
            <label htmlFor="q" className="sr-only">Ask a question</label>
            <textarea id="q" ref={inputRef} rows={1} value={question} onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={onKey} placeholder="Ask about your project documents…"
                      className="input max-h-40 min-h-[42px] resize-none" autoFocus />
            <button className="btn-primary h-[42px]" disabled={busy || !question.trim()} aria-label="Ask">
              <ArrowUp className="h-4 w-4" aria-hidden /> Ask
            </button>
          </div>
        </form>
      </section>

      {deleting && (
        <Confirm title="Delete chat?" danger confirmLabel="Delete"
                 message={<>This deletes <strong>{deleting.title}</strong> and all its questions and answers.</>}
                 onConfirm={() => deleteConversation(deleting)} onCancel={() => setDeleting(null)} />
      )}
    </div>
  );
}

function EmptyState({ onPick, hasProjects }: { onPick: (q: string) => void; hasProjects: boolean }) {
  return (
    <div className="pt-10">
      <h1 className="text-[24px] leading-tight">Ask about your project documents</h1>
      <p className="mt-2 text-muted">
        Answers come only from the documents in your selected folders, followed by the file and page they came from. If the folders don't cover a question, InfoPoint says so instead of guessing.
      </p>
      {!hasProjects && (
        <p className="mt-4 rounded-md border border-line bg-surface p-3 text-[14.5px]">
          No document folders are available yet. An administrator can add one under Settings.
        </p>
      )}
      <h2 className="mb-2 mt-8 text-[12.5px] font-semibold uppercase tracking-wide text-muted">For example</h2>
      <ul className="space-y-2">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <button onClick={() => onPick(q)} className="card w-full px-4 py-3 text-left text-[15px] hover:border-accent">
              {q}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
