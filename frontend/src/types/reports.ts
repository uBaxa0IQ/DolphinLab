export type ResultKind =
  | "dataset"
  | "table"
  | "metric"
  | "finding"
  | "chart"
  | "report"
  | "ml_candidate";
export type Column = {
  name: string;
  label: string;
  type: "string" | "number";
  unit: string;
};
export type TableData = {
  columns: Column[];
  rows: (string | number | null)[][];
  selection: string;
  sample_size: number;
};
export type ChartData = {
  type: "bar" | "line" | "scatter";
  table_id: string;
  x: string;
  y: string;
  x_label: string;
  y_label: string;
  caption: string;
};
export type ReportData = {
  summary: string;
  blocks: { result_id: string; caption: string; required: boolean }[];
  limitations: string[];
};
export type ResultSummary = {
  id: string;
  kind: ResultKind;
  title: string;
  revision: number;
  sha256: string;
  artifact_id: string;
  job_id?: string;
  author: string;
  legacy?: boolean;
  status?: string;
};
export type Assessment = {
  id: string;
  status: string;
  reviewer: string;
  job_id: string;
  artifact_id: string;
  conclusion: string;
  checks: { name: string; passed: boolean; detail: string }[];
};
export type ResultRecord = ResultSummary & {
  payload: unknown;
  status: string;
  applicable: boolean;
  assessment: Assessment | null;
  dependencies: string[];
  evidence_files: { label: string; path: string }[];
  input_versions: { id: string; name: string; sha256: string }[];
};
export type PageRevision = {
  id: string;
  report_id: string;
  title: string;
  draft: boolean;
  outcome: string;
  created: number;
  criteria: string[];
  statuses: Record<string, string>;
};
export type ResultIndex = {
  results: ResultSummary[];
  pages: PageRevision[];
  current: { page_id: string } | null;
  contract: { kind: string; criteria: string[] };
};
