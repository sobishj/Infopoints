import { useCallback, useEffect, useState } from "react";
import { RotateCw } from "lucide-react";

import { useSession } from "../App";
import { api } from "../api/client";
import type { LibraryFile, Project } from "../api/types";
import { formatBytes, formatWhen } from "../format";

interface LibraryResponse { files: LibraryFile[]; summary: Record<string, number> }

export default function LibraryPage() {
  const { user } = useSession();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | "">("");
  const [data, setData] = useState<LibraryResponse | null>(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    const q = projectId ? `?project_id=${projectId}` : "";
    setData(await api<LibraryResponse>(`/api/library${q}`));
  }, [projectId]);

  useEffect(() => { api<{ projects: Project[] }>("/api/projects").then((r) => setProjects(r.projects)); }, []);

  const busy = (data?.summary.queued ?? 0) + (data?.summary.processing ?? 0) > 0;
  useEffect(() => {
    load();
    const t = setInterval(load, busy ? 2500 : 15000);
    return () => clearInterval(t);
  }, [load, busy]);

  const reindex = async (f: LibraryFile) => {
    await api(`/api/files/${f.id}/reindex`, { method: "POST" });
    load();
  };

  const files = (data?.files ?? []).filter((f) => !filter || f.rel_path.toLowerCase().includes(filter.toLowerCase()));
  const s = data?.summary ?? {};

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[22px]">Library</h1>
            <p className="text-[14.5px] text-muted" aria-live="polite">
              {s.indexed ?? 0} indexed
              {(s.processing ?? 0) + (s.queued ?? 0) > 0 && ` · ${(s.processing ?? 0) + (s.queued ?? 0)} being indexed`}
              {(s.failed ?? 0) > 0 && ` · ${s.failed} failed`}
            </p>
          </div>
          <div className="flex gap-2">
            <label className="sr-only" htmlFor="proj">Project</label>
            <select id="proj" className="input w-56" value={projectId} onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : "")}>
              <option value="">All projects</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <label className="sr-only" htmlFor="filter">Filter files</label>
            <input id="filter" className="input w-56" placeholder="Filter by name…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
        </div>

        <div className="card mt-6 overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr><th>File</th><th>Type</th><th>Size</th><th>Status</th><th>Indexed</th>{user.role === "admin" && <th />}</tr>
            </thead>
            <tbody>
              {data && files.length === 0 && (
                <tr><td colSpan={6} className="text-muted">No files yet. Files appear here as soon as a folder is scanned.</td></tr>
              )}
              {files.map((f) => (
                <tr key={f.id}>
                  <td className="max-w-[420px]">
                    <a href={f.kind === "text" ? `/api/files/${f.id}/content` : `/api/files/${f.id}/pdf`} target="_blank" rel="noreferrer"
                       className="link break-all">{f.file_name}</a>
                    {f.rel_path !== f.file_name && <div className="break-all text-[13px] text-muted">{f.rel_path}</div>}
                  </td>
                  <td className="text-[14px] uppercase text-muted">{f.ext.replace(".", "")}</td>
                  <td className="whitespace-nowrap text-[14px] text-muted">{formatBytes(f.size_bytes)}</td>
                  <td className="min-w-[200px]"><Status f={f} /></td>
                  <td className="whitespace-nowrap text-[14px] text-muted">
                    {formatWhen(f.indexed_at)}{f.page_count ? <><br />{f.page_count} pages</> : null}
                  </td>
                  {user.role === "admin" && (
                    <td className="text-right">
                      <button className="btn-quiet px-2 py-1" title="Re-index this file" aria-label={`Re-index ${f.file_name}`}
                              onClick={() => reindex(f)} disabled={f.status === "processing"}>
                        <RotateCw className="h-4 w-4" aria-hidden />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Status({ f }: { f: LibraryFile }) {
  if (f.status === "processing") {
    const pct = Math.round((f.progress ?? 0) * 100);
    return (
      <div>
        <div className="text-[14px]">Indexing… {pct}%</div>
        <div className="mt-1 h-1.5 w-full rounded bg-line" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-1.5 rounded bg-accent transition-all" style={{ width: `${pct}%` }} />
        </div>
        {f.progress_msg && <div className="mt-0.5 text-[12.5px] text-muted">{f.progress_msg}</div>}
      </div>
    );
  }
  if (f.status === "failed") return <div className="text-[14px] text-danger" title={f.error ?? ""}>Failed<div className="text-[12.5px] text-muted">{f.error}</div></div>;
  if (f.status === "queued") return <span className="text-[14px] text-muted">Waiting</span>;
  return (
    <span className="text-[14px]">
      Indexed{f.error && <span className="block text-[12.5px] text-muted">{f.error}</span>}
    </span>
  );
}
