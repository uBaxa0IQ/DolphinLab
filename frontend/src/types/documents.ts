export interface DocumentVersion {
  id: string;
  document_id: string;
  revision: number;
  current: boolean;
  name: string;
  sha256: string;
  size: number;
  state: "queued" | "processing" | "ready" | "failed" | "deleted";
  pipeline: string;
  error: string | null;
  warnings: string[];
  usage: { chunks?: number; elapsed_seconds?: number };
}

export interface KnowledgeSource {
  source_id: string;
  kind?: string;
  name?: string;
  revision?: number;
  version_id?: string;
  sha256?: string;
  text?: string;
  current?: boolean;
  locator?: Record<string, unknown>;
  warnings?: string[];
  citation?: string;
  notice?: string;
  neighbors?: KnowledgeSource[];
}
