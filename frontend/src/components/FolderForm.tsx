import { useState } from "react";
import { FolderOpen } from "lucide-react";

import { api } from "../api/client";
import type { Folder, FolderPreview } from "../api/types";
import { formatBytes, formatDuration } from "../format";
import { FolderBrowser } from "./FolderBrowser";
import { Confirm } from "./Modal";

const TYPE_LABELS: Record<string, string> = { pdf: "PDF", word: "Word / ODT", powerpoint: "PowerPoint", text: "Text / Markdown" };

declare global {
  interface Window { pywebview?: { api?: { pick_folder?: () => Promise<string | null> } } }
}

export interface FolderFormValues {
  display_name: string;
  host_path: string;
  include_subfolders: boolean;
  file_type_filter: string[];
}

export function FolderForm({ initial, existing, allowedRoots, submitLabel, onSaved, onCancel }: {
  initial?: Partial<FolderFormValues>;
  existing?: Folder;
  allowedRoots: string[];
  submitLabel: string;
  onSaved: (f: Folder) => void;
  onCancel?: () => void;
}) {
  const [v, setV] = useState<FolderFormValues>({
    display_name: existing?.display_name ?? initial?.display_name ?? "",
    host_path: existing?.host_path ?? initial?.host_path ?? "",
    include_subfolders: existing?.include_subfolders ?? true,
    file_type_filter: existing?.file_type_filter ?? [],
  });
  const [preview, setPreview] = useState<FolderPreview | null>(null);
  const [checkedPath, setCheckedPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [confirmPath, setConfirmPath] = useState(false);

  const pathChanged = existing ? v.host_path.trim() !== existing.host_path : true;
  const needsCheck = pathChanged && checkedPath !== v.host_path.trim();

  const set = (patch: Partial<FolderFormValues>) => { setV((s) => ({ ...s, ...patch })); setError(null); };

  const browse = async () => {
    const native = window.pywebview?.api?.pick_folder; // desktop launcher: native OS dialog
    if (native) {
      const picked = await native();
      if (picked) { set({ host_path: picked }); check(picked); }
    } else setBrowsing(true);
  };

  const check = async (path = v.host_path) => {
    setBusy(true);
    setError(null);
    setPreview(null);
    try {
      const p = await api<FolderPreview>("/api/folders/validate", {
        method: "POST",
        body: { host_path: path, include_subfolders: v.include_subfolders, file_type_filter: v.file_type_filter },
      });
      setPreview(p);
      setCheckedPath(path.trim());
      if (!v.display_name) {
        const name = p.host_path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";
        set({ display_name: name, host_path: path });
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = { ...v, file_type_filter: v.file_type_filter.length ? v.file_type_filter : null };
      const saved = existing
        ? await api<Folder>(`/api/folders/${existing.id}`, { method: "PATCH", body })
        : await api<Folder>("/api/folders", { method: "POST", body });
      onSaved(saved);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleType = (t: string) =>
    set({ file_type_filter: v.file_type_filter.includes(t) ? v.file_type_filter.filter((x) => x !== t) : [...v.file_type_filter, t] });

  return (
    <div className="space-y-4">
      <div>
        <label className="label" htmlFor="fpath">Folder path</label>
        <div className="flex gap-2">
          <input id="fpath" className="input" value={v.host_path} placeholder="e.g. C:\Projects\Procurement or \\fileserver\projects"
                 onChange={(e) => { set({ host_path: e.target.value }); setPreview(null); }}
                 onKeyDown={(e) => e.key === "Enter" && check()} />
          <button type="button" className="btn-secondary shrink-0" onClick={browse}>
            <FolderOpen className="h-4 w-4" aria-hidden /> Browse…
          </button>
        </div>
        <p className="mt-1 text-[13px] text-muted">
          Allowed locations: {allowedRoots.join(", ") || "none"}. To allow another drive or share, add it to
          ALLOWED_DOC_ROOTS in the .env file and restart InfoPoint.
        </p>
      </div>
      <div>
        <label className="label" htmlFor="fname">Project name</label>
        <input id="fname" className="input" value={v.display_name} placeholder="Shown in the project selector"
               onChange={(e) => set({ display_name: e.target.value })} />
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-[14.5px]">
        <label className="flex items-center gap-2">
          <input type="checkbox" className="accent-accent" checked={v.include_subfolders}
                 onChange={(e) => { set({ include_subfolders: e.target.checked }); setPreview(null); setCheckedPath(null); }} />
          Include subfolders
        </label>
      </div>
      <fieldset>
        <legend className="label">File types (none selected = all supported types)</legend>
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-[14.5px]">
          {Object.entries(TYPE_LABELS).map(([k, label]) => (
            <label key={k} className="flex items-center gap-2">
              <input type="checkbox" className="accent-accent" checked={v.file_type_filter.includes(k)}
                     onChange={() => { toggleType(k); setPreview(null); setCheckedPath(null); }} />
              {label}
            </label>
          ))}
        </div>
      </fieldset>

      {preview && (
        <div className="rounded-md border border-line bg-page p-3 text-[14.5px]" aria-live="polite">
          <p>
            <strong>{preview.total_files.toLocaleString()}</strong> supported file{preview.total_files === 1 ? "" : "s"}
            {" · "}{formatBytes(preview.total_bytes)}{preview.partial ? " (partial count — very large folder)" : ""}
          </p>
          {preview.total_files > 0 && (
            <p className="mt-1 text-muted">
              {Object.entries(preview.counts).map(([ext, n]) => `${n} ${ext.toUpperCase()}`).join(" · ")}
              {" — "}first indexing takes {formatDuration(preview.estimated_seconds)}.
            </p>
          )}
        </div>
      )}
      {error && <p className="text-[14px] text-danger" role="alert">{error}</p>}

      <div className="flex justify-end gap-2">
        {onCancel && <button className="btn-secondary" onClick={onCancel}>Cancel</button>}
        <button className="btn-secondary" disabled={busy || !v.host_path.trim()} onClick={() => check()}>Check folder</button>
        <button className="btn-primary" disabled={busy || !v.host_path.trim() || needsCheck}
                onClick={() => (existing && pathChanged ? setConfirmPath(true) : save())}
                title={needsCheck ? "Check the folder first" : undefined}>
          {submitLabel}
        </button>
      </div>

      {browsing && (
        <FolderBrowser start={v.host_path || undefined} onClose={() => setBrowsing(false)}
                       onPick={(p) => { setBrowsing(false); set({ host_path: p }); check(p); }} />
      )}
      {confirmPath && (
        <Confirm title="Change folder location?" confirmLabel="Change and re-index"
                 message={<>The index for <strong>{existing?.host_path}</strong> will be removed and the new location
                   will be indexed from scratch. Answers from this project are unavailable until indexing finishes.</>}
                 onCancel={() => setConfirmPath(false)} onConfirm={() => { setConfirmPath(false); save(); }} />
      )}
    </div>
  );
}
