export interface User {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "user";
}

export interface AppStatus {
  mode: string;
  first_run: boolean;
  folder_count: number;
  default_folder: string | null;
  embedding_model: string;
  is_admin: boolean;
}

export interface Project {
  id: number;
  name: string;
  indexed: number;
  pending: number;
}

export interface SourceCard {
  n: number;
  chunk_id: number | null;
  file_id: number | null;
  file_name: string;
  kind?: string;
  ext?: string;
  label: string;
  page: number | null;
  t_start: number | null;
  snippet: string;
  open_url: string;
  cited: boolean;
  source_changed?: boolean;
}

export type TurnStatus = "streaming" | "ok" | "not_found" | "error" | "no_projects";

export interface Turn {
  key: string;
  question: string;
  text: string;
  status: TurnStatus;
  sources: SourceCard[];
  cited: number[];
  uncited?: boolean;
  error?: string;
}

export interface Folder {
  id: number;
  display_name: string;
  host_path: string;
  enabled: boolean;
  include_subfolders: boolean;
  file_type_filter: string[];
  last_scan_at: string | null;
  last_scan_status: string | null;
  files?: { indexed: number; queued: number; processing: number; failed: number };
}

export interface FolderPreview {
  host_path: string;
  counts: Record<string, number>;
  total_files: number;
  total_bytes: number;
  estimated_seconds: number;
  partial: boolean;
}

export interface LibraryFile {
  id: number;
  project_id: number;
  rel_path: string;
  file_name: string;
  ext: string;
  kind: string;
  size_bytes: number;
  status: "queued" | "processing" | "indexed" | "failed";
  error: string | null;
  page_count: number | null;
  indexed_at: string | null;
  progress: number | null;
  progress_msg: string | null;
}

export interface Conversation {
  id: number;
  title: string;
  updated_at: string;
}
