export type ProjectPolicy = {
  revision: number;
  strategy: {
    exploration: "focused" | "balanced" | "exploratory";
    guidance: string;
  };
  limits: {
    max_teams: number;
    parallel_jobs: number;
    parallel_model_calls: number;
    cpu_total: number;
    ram_gb: number;
    duration_minutes: number;
    api_budget?: number | null;
  };
};
export type WorkspaceSettings = {
  display_name: string;
  base_url: string;
  model: string;
  key_configured: boolean;
  execution_enabled: boolean;
  pricing?: {
    currency: string;
    input_per_million: number;
    output_per_million: number;
    max_input_tokens: number;
    max_output_tokens: number;
  };
};
export type Member = {
  name: string;
  role: string;
  state: string;
  color: string;
};
export type Team = {
  id: string;
  name: string;
  focus: string;
  icon: "layers" | "sparkles" | "shield";
  state: string;
  stage: string;
  current: string;
  members: Member[];
  updated: string;
  completed: number;
  total: number;
};
export type Experiment = {
  id: string;
  name: string;
  team: string;
  status:
    "running" | "verified" | "rejected" | "succeeded" | "failed" | "cancelled";
  score: string;
  duration: string;
  detail: string;
};
export type FeedEvent = {
  id: string;
  time: string;
  title: string;
  detail: string;
  type: "result" | "team" | "info";
};
export type InputFile = {
  id: string;
  name: string;
  size: number;
  kind: string;
};
export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  time: string;
  directive?: boolean;
  status?: string;
  error?: string;
  directive_status?: string;
  directive_decision?: string;
};
export type ResearchRun = {
  id: string;
  iteration_id: string;
  goal_snapshot: GoalSpec;
  status: string;
  elapsed_seconds: number;
  created: string;
  updated: string;
  error?: string | null;
  policy: ProjectPolicy;
  usage: {
    requests?: number;
    accounted_cost?: number;
    reserved_cost?: number;
    currency?: string;
    unknown_requests?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
  };
  publication?: { candidate_id: string; sha256: string } | null;
};
export type Project = {
  id: string;
  name: string;
  description: string;
  status: string;
  demo?: boolean;
  archived?: boolean;
  metric?: string;
  score?: string;
  teams: Team[];
  experiments: Experiment[];
  events: FeedEvent[];
  files: InputFile[];
  latest_run?: ResearchRun | null;
  current_goal?: GoalRevision | null;
};

export type GoalSpec = {
  schema_version: 1;
  question: string;
  expected_outputs: string[];
  definitions: Record<string, string>;
  constraints: string[];
  open_questions: string[];
};
export type GoalRevision = {
  id: string;
  project_id: string;
  revision: number;
  goal: GoalSpec;
  origin: "user" | "project" | "legacy";
  created: string;
};
export type ResearchIteration = {
  id: string;
  project_id: string;
  sequence: number;
  parent_iteration_id: string | null;
  goal: GoalRevision;
  legacy: boolean;
  status: string;
  created: string;
  runs: ResearchRun[];
};
