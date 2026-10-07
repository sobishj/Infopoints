import { useEffect, useState } from "react";
import { ChevronRight, Folder as FolderIcon } from "lucide-react";

import { api } from "../api/client";
import { Modal } from "./Modal";

interface Listing {
  path: string | null;
  parent: string | null;
  breadcrumbs: { name: string; path: string }[];
  entries: { name: string; path: string }[];
}

/** Server-side folder browser, limited to the allowed roots. */
export function FolderBrowser({ start, onPick, onClose }: {
  start?: string; onPick: (path: string) => void; onClose: () => void;
}) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = async (path?: string | null) => {
    setError(null);
    try {
      setListing(await api<Listing>(`/api/fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`));
    } catch (e) {
      setError((e as Error).message);
      if (path) setListing(await api<Listing>("/api/fs/list"));
    }
  };

  useEffect(() => { open(start || null); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Modal title="Choose a folder" onClose={onClose} wide>
      <nav className="mb-2 flex flex-wrap items-center gap-1 text-[14px]" aria-label="Breadcrumbs">
        <button className="link" onClick={() => open(null)}>Roots</button>
        {listing?.breadcrumbs.map((b) => (
          <span key={b.path} className="flex items-center gap-1">
            <ChevronRight className="h-3.5 w-3.5 text-muted" aria-hidden />
            <button className="link" onClick={() => open(b.path)}>{b.name}</button>
          </span>
        ))}
      </nav>
      {error && <p className="mb-2 text-[14px] text-danger">{error}</p>}
      <ul className="h-80 overflow-y-auto rounded-md border border-line">
        {listing?.entries.length === 0 && <li className="p-3 text-[14px] text-muted">No sub-folders.</li>}
        {listing?.entries.map((e) => (
          <li key={e.path}>
            <button onClick={() => open(e.path)}
                    className="flex w-full items-center gap-2 border-b border-line px-3 py-2 text-left text-[14.5px] last:border-b-0 hover:bg-page">
              <FolderIcon className="h-4 w-4 text-muted" aria-hidden /> {e.name}
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex items-center justify-between gap-2">
        <span className="truncate text-[14px] text-muted">{listing?.path ?? "Pick a root to start"}</span>
        <div className="flex shrink-0 gap-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!listing?.path} onClick={() => listing?.path && onPick(listing.path)}>
            Select this folder
          </button>
        </div>
      </div>
    </Modal>
  );
}
