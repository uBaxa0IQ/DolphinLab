export type PageBlock = {
  id: string;
  run_id: string;
  result_id: string;
  sha256: string;
  caption: string;
  width: "full" | "half";
  required: boolean;
  filter_text: string;
  origin_revision_id: string | null;
};
export type PageSpec = {
  schema_version: 1;
  title: string;
  blocks: PageBlock[];
};
export type UserState = {
  pins: Record<string, PageBlock>;
  comments: Record<
    string,
    { id: string; text: string; block_id: string | null; author: string }
  >;
};
export type DraftRevision = {
  id: string;
  revision: number;
  page: PageSpec;
  author: Record<string, unknown>;
  feedback_revision: number;
  created: string;
};
export type PageManifest = {
  outcome: "complete" | "inconclusive";
  sources: {
    run_id: string;
    result_id: string;
    sha256: string;
    status: string;
    assessment_id: string | null;
  }[];
  needs_review: boolean;
  notices: { block_id: string; required: boolean; reason: string }[];
};
export type PageRelease = {
  id: string;
  revision_id: string;
  revision: number;
  page: PageSpec;
  manifest: PageManifest;
  current_evidence: PageManifest;
  user_state: UserState;
  created: string;
};
export type PageSummary = {
  id: string;
  kind: "main" | "team";
  run_id: string | null;
  team_id: string | null;
  revision: number;
  feedback_revision: number;
  published_id: string | null;
  title: string;
};
export type PageRecord = Omit<PageSummary, "title"> & {
  draft: DraftRevision;
  publication: PageRelease | null;
  revisions: DraftRevision[];
  history: { id: string; revision_id: string; created: string }[];
  user_state: UserState;
};
export type PageCatalog = {
  runs: {
    id: string;
    question: string;
    definitions: Record<string, string>;
    document_sources: unknown[];
    inputs: { name: string; sha256: string }[];
    status: string;
    created: string;
  }[];
  teams: { id: string; run_id: string; name: string; state: string }[];
};
