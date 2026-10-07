import { useCallback, useEffect, useState } from "react";
import { Plus } from "lucide-react";

import { useSession } from "../App";
import { api } from "../api/client";
import type { Folder } from "../api/types";
import { FolderForm } from "../components/FolderForm";
import { Confirm, Modal } from "../components/Modal";
import { formatWhen } from "../format";

interface FoldersResponse { folders: Folder[]; allowed_roots: string[]; default_folder: string | null }
interface AppSettings { rescan_interval_seconds: number; whisper_model: string; embedding_model: string }

export default function SettingsPage() {
  const { refreshStatus } = useSession();
  const [data, setData] = useState<FoldersResponse | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [editing, setEditing] = useState<Folder | "new" | null>(null);
  const [removing, setRemoving] = useState<Folder | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setData(await api<FoldersResponse>("/api/folders"));
  }, []);

  useEffect(() => {
    load();
    api<AppSettings>("/api/settings").then(setSettings);
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const flash = (m: string) => { setMessage(m); setTimeout(() => setMessage(null), 4000); };

  const toggle = async (f: Folder) => {
    await api(`/api/folders/${f.id}`, { method: "PATCH", body: { enabled: !f.enabled } });
    flash(f.enabled ? `“${f.display_name}” is hidden from search. Its index is kept.` : `“${f.display_name}” is searchable again.`);
    load();
  };
  const rescan = async (f: Folder) => {
    await api(`/api/folders/${f.id}/rescan`, { method: "POST" });
    flash(`Rescanning “${f.display_name}”.`);
  };
  const remove = async (f: Folder) => {
    await api(`/api/folders/${f.id}`, { method: "DELETE" });
    setRemoving(null);
    flash(`“${f.display_name}” and its index were removed.`);
    await load();
    refreshStatus();
  };
  const saveSettings = async (patch: Partial<AppSettings>) => {
    setSettings(await api<AppSettings>("/api/settings", { method: "PUT", body: patch }));
    flash("Settings saved.");
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-8">
        <div className="flex items-center justify-between">
          <h1 className="text-[22px]">Settings</h1>
          {message && <p className="text-[14px] text-accent" role="status">{message}</p>}
        </div>

        {/* ---------------------------------------------------------------- folders */}
        <section className="mt-8">
          <div className="mb-3 flex items-end justify-between">
            <div>
              <h2 className="text-[17px]">Document folders</h2>
              <p className="text-[14px] text-muted">Each folder is a project. New and changed files are indexed automatically.</p>
            </div>
            <button className="btn-primary" onClick={() => setEditing("new")}><Plus className="h-4 w-4" aria-hidden /> Add folder</button>
          </div>
          <div className="card overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr><th>Project</th><th>Path</th><th>Files</th><th>Last scan</th><th className="text-right">Actions</th></tr>
              </thead>
              <tbody>
                {data?.folders.length === 0 && (
                  <tr><td colSpan={5} className="text-muted">No folders yet. Add your first document folder to start.</td></tr>
                )}
                {data?.folders.map((f) => (
                  <tr key={f.id} className={f.enabled ? "" : "text-muted"}>
                    <td className="font-semibold">{f.display_name}{!f.enabled && <span className="ml-2 text-[12.5px] font-normal">(disabled)</span>}</td>
                    <td className="max-w-[280px] break-all text-[14px]">{f.host_path}</td>
                    <td className="whitespace-nowrap text-[14px]">
                      {f.files?.indexed ?? 0} indexed
                      {(f.files?.queued || f.files?.processing) ? <span className="text-muted"> · {(f.files?.queued ?? 0) + (f.files?.processing ?? 0)} pending</span> : null}
                      {f.files?.failed ? <span className="text-danger"> · {f.files.failed} failed</span> : null}
                    </td>
                    <td className="whitespace-nowrap text-[14px] text-muted">{formatWhen(f.last_scan_at)}<br />{f.last_scan_status}</td>
                    <td className="whitespace-nowrap text-right">
                      <button className="btn-quiet px-2 py-1" onClick={() => setEditing(f)}>Edit</button>
                      <button className="btn-quiet px-2 py-1" onClick={() => rescan(f)} disabled={!f.enabled}>Rescan</button>
                      <button className="btn-quiet px-2 py-1" onClick={() => toggle(f)}>{f.enabled ? "Disable" : "Enable"}</button>
                      <button className="btn-quiet px-2 py-1 text-danger" onClick={() => setRemoving(f)}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[13px] text-muted">
            Disabling hides a project from search but keeps its index. Removing deletes the index (your files are never touched).
          </p>
        </section>

        {/* ---------------------------------------------------------------- general */}
        {settings && (
          <section className="mt-10">
            <h2 className="text-[17px]">Indexing</h2>
            <div className="card mt-3 divide-y divide-line">
              <div className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div>
                  <div className="font-semibold">Full rescan interval</div>
                  <div className="text-[14px] text-muted">Changes are noticed within ~20 s; a full rescan also catches anything network shares miss.</div>
                </div>
                <select className="input w-44" value={settings.rescan_interval_seconds}
                        onChange={(e) => saveSettings({ rescan_interval_seconds: Number(e.target.value) })}>
                  {[60, 300, 900, 1800, 3600].map((s) => <option key={s} value={s}>Every {s / 60} min</option>)}
                </select>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div>
                  <div className="font-semibold">Speech-to-text model (audio and video)</div>
                  <div className="text-[14px] text-muted">Larger is more accurate but slower. Audio and video indexing arrives in Phase 2.</div>
                </div>
                <select className="input w-44" value={settings.whisper_model}
                        onChange={(e) => saveSettings({ whisper_model: e.target.value })}>
                  {["tiny", "base", "small", "medium"].map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div>
                  <div className="font-semibold">Embedding model</div>
                  <div className="text-[14px] text-muted">Set with EMBEDDING_MODEL in .env. Changing it requires re-indexing every file.</div>
                </div>
                <code className="rounded bg-page px-2 py-1 text-[14px]">{settings.embedding_model}</code>
              </div>
            </div>
          </section>
        )}

        <section className="mt-10 mb-6">
          <h2 className="text-[17px]">Allowed locations</h2>
          <p className="mt-1 text-[14px] text-muted">
            InfoPoint can only read folders inside: <strong className="text-ink">{data?.allowed_roots.join(", ")}</strong>.
            To add a drive or network share, append it to <code>ALLOWED_DOC_ROOTS</code> in the <code>.env</code> file
            (comma-separated) and run <code>scripts\start.ps1</code> again. Locations are mounted read-only.
          </p>
        </section>
      </div>

      {editing && data && (
        <Modal title={editing === "new" ? "Add document folder" : `Edit “${editing.display_name}”`} onClose={() => setEditing(null)} wide>
          <FolderForm existing={editing === "new" ? undefined : editing} allowedRoots={data.allowed_roots}
                      submitLabel={editing === "new" ? "Save and start indexing" : "Save"}
                      onCancel={() => setEditing(null)}
                      onSaved={(f) => { setEditing(null); flash(`Saved “${f.display_name}”. Indexing runs in the background.`); load(); refreshStatus(); }} />
        </Modal>
      )}
      {removing && (
        <Confirm title={`Remove “${removing.display_name}”?`} danger confirmLabel="Remove folder and index"
                 message={<>This deletes InfoPoint's index for <strong>{removing.host_path}</strong>. The files themselves are not touched. Answers will no longer cite them.</>}
                 onCancel={() => setRemoving(null)} onConfirm={() => remove(removing)} />
      )}
    </div>
  );
}
