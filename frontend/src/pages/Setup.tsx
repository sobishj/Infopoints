import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useSession } from "../App";
import { api } from "../api/client";
import { FolderForm } from "../components/FolderForm";

/** First run: the user picks the folder InfoPoint learns from; skipping uses the system Downloads folder. */
export default function SetupPage() {
  const { status, refreshStatus } = useSession();
  const navigate = useNavigate();
  const [roots, setRoots] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ allowed_roots: string[] }>("/api/folders").then((r) => setRoots(r.allowed_roots));
  }, []);

  const done = async () => {
    await refreshStatus();
    navigate("/library");
  };

  const useDownloads = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/setup/default-folder", { method: "POST" });
      await done();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-12">
        <h1 className="text-[24px]">Add your first document folder</h1>
        <p className="mt-2 text-muted">
          InfoPoint answers questions using only the documents in the folders you choose. Pick the folder with your
          project documents — PDFs, Word and PowerPoint files, text and Markdown. Indexing starts as soon as you save,
          and new or changed files are picked up automatically.
        </p>
        <div className="card mt-8 p-6">
          <FolderForm
            initial={{ host_path: status.default_folder ?? "", display_name: status.default_folder ? "Downloads" : "" }}
            allowedRoots={roots} submitLabel="Start indexing" onSaved={done} />
        </div>
        {status.default_folder && (
          <div className="mt-6 flex flex-wrap items-center gap-3 text-[14.5px]">
            <span className="text-muted">Not sure yet?</span>
            <button className="btn-quiet" disabled={busy} onClick={useDownloads}>
              Skip — use my Downloads folder
            </button>
            <span className="text-muted">You can add or change folders later in Settings.</span>
          </div>
        )}
        {error && <p className="mt-3 text-[14px] text-danger" role="alert">{error}</p>}
      </div>
    </div>
  );
}
