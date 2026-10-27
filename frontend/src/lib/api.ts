import type {
  ChatMessage,
  ChatIntent,
  MemoryRecord,
  GoalRevision,
  GoalSpec,
  InputFile,
  Project,
  ProjectPolicy,
  ResearchRun,
  ResearchIteration,
  WorkspaceSettings,
} from "../types";
import type { ResultIndex, ResultRecord } from "../types/reports";
import type { DocumentVersion, KnowledgeSource } from "../types/documents";
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch("/api" + path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `Сервер недоступен (${response.status})`,
    );
  }
  return response.json();
}
const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const pendingDeliveries = new Map<string, string>();
const pendingOperations = new Map<string, string>();
async function createOnce<T>(
  key: string,
  path: string,
  body: object,
): Promise<T> {
  const client_id = pendingOperations.get(key) || crypto.randomUUID();
  pendingOperations.set(key, client_id);
  const result = await request<T>(path, json({ ...body, client_id }));
  pendingOperations.delete(key);
  return result;
}
export const api = {
  documents: (id: string) =>
    request<DocumentVersion[]>(`/projects/${id}/documents`),
  uploadDocument: async (
    id: string,
    file: File,
    previous?: DocumentVersion,
  ) => {
    const key = JSON.stringify([
      "document",
      id,
      file.name,
      file.size,
      file.lastModified,
      previous?.id,
    ]);
    const clientId = pendingOperations.get(key) || crypto.randomUUID();
    pendingOperations.set(key, clientId);
    const form = new FormData();
    form.set("file", file);
    form.set("client_id", clientId);
    form.set("expected_revision", String(previous?.revision || 0));
    if (previous) form.set("document_id", previous.document_id);
    const result = await request<DocumentVersion>(`/projects/${id}/documents`, {
      method: "POST",
      body: form,
    });
    pendingOperations.delete(key);
    return result;
  },
  retryDocument: (id: string, version: string) =>
    request<DocumentVersion>(
      `/projects/${id}/documents/retry`,
      json({ version_id: version }),
    ),
  removeDocument: (id: string, version: DocumentVersion) =>
    request<{ notice: string }>(
      `/projects/${id}/documents/${version.document_id}`,
      { ...json({ expected_revision: version.revision }), method: "DELETE" },
    ),
  searchKnowledge: (
    id: string,
    query: string,
    version?: string,
    historical = false,
  ) =>
    request<{ results: KnowledgeSource[] }>(
      `/projects/${id}/knowledge/search`,
      json({
        query,
        kind: "documents",
        version_id: version || null,
        include_historical: historical,
      }),
    ),
  readSource: (id: string, source: string, neighbors = 1) =>
    request<KnowledgeSource>(
      `/projects/${id}/knowledge/sources/${encodeURIComponent(source)}?neighbors=${neighbors}`,
    ),
  results: (id: string, run: string) =>
    request<ResultIndex>(`/projects/${id}/runs/${run}/results`),
  result: (id: string, run: string, result: string) =>
    request<ResultRecord>(`/projects/${id}/runs/${run}/results/${result}`),
  runs: (id: string) => request<ResearchRun[]>(`/projects/${id}/runs`),
  start: (id: string, iterationId?: string, previousRunId?: string) =>
    createOnce<ResearchRun>(
      JSON.stringify(["start", id, iterationId, previousRunId]),
      `/projects/${id}/runs`,
      { iteration_id: iterationId || null },
    ),
  goals: (id: string) => request<GoalRevision[]>(`/projects/${id}/goals`),
  createGoal: (id: string, expected_revision: number, goal: GoalSpec) =>
    createOnce<GoalRevision>(
      JSON.stringify(["goal", id, expected_revision, goal]),
      `/projects/${id}/goals`,
      { expected_revision, goal },
    ),
  iterations: (id: string) =>
    request<ResearchIteration[]>(`/projects/${id}/iterations`),
  createIteration: (id: string, goal: string, previous?: ResearchIteration) =>
    createOnce<ResearchIteration>(
      JSON.stringify(["iteration", id, goal, previous?.id]),
      `/projects/${id}/iterations`,
      {
        goal_revision_id: goal,
        expected_sequence: previous?.sequence || 0,
        parent_iteration_id: previous?.id || null,
      },
    ),
  control: (id: string, run: string, action: string) =>
    request<ResearchRun>(
      `/projects/${id}/runs/${run}/control`,
      json({ action }),
    ),
  runBudget: (
    id: string,
    run: string,
    amount: number,
    revision: number,
    clientId: string,
  ) =>
    request<ResearchRun>(`/projects/${id}/runs/${run}/budget`, {
      ...json({
        api_budget: amount,
        expected_revision: revision,
        client_id: clientId,
      }),
      method: "PUT",
    }),
  artifacts: (id: string, run: string) =>
    request<{ path: string; size: number }[]>(
      `/projects/${id}/runs/${run}/artifacts`,
    ),
  retry: (id: string, message: string) =>
    request<ChatMessage>(`/projects/${id}/messages/${message}/retry`, json({})),
  projectSettings: (id: string) =>
    request<ProjectPolicy>(`/projects/${id}/settings`),
  saveProjectSettings: (id: string, settings: ProjectPolicy) =>
    request<ProjectPolicy>(`/projects/${id}/settings`, {
      ...json(settings),
      method: "PUT",
    }),
  settings: () => request<WorkspaceSettings>("/settings"),
  saveSettings: (settings: {
    display_name: string;
    base_url: string;
    model: string;
    api_key?: string;
    clear_key?: boolean;
    pricing?: WorkspaceSettings["pricing"];
  }) =>
    request<WorkspaceSettings>("/settings", {
      ...json(settings),
      method: "PUT",
    }),
  projects: () => request<Project[]>("/projects"),
  editProject: (
    id: string,
    data: {
      name: string;
      description: string;
      archived: boolean;
      expected_goal_revision?: number;
    },
  ) => request<Project>(`/projects/${id}`, { ...json(data), method: "PATCH" }),
  deleteProject: (id: string) =>
    request<{ deleted: boolean; files_removed: boolean }>(`/projects/${id}`, {
      method: "DELETE",
    }),
  project: (id: string) => request<Project>(`/projects/${id}`),
  create: (name: string, description: string) =>
    request<Project>("/projects", json({ name, description })),
  messages: (id: string) => request<ChatMessage[]>(`/projects/${id}/messages`),
  memory: (id: string, offset = 0) =>
    request<MemoryRecord[]>(
      `/projects/${id}/memory?include_stale=true&offset=${offset}`,
    ),
  send: async (
    id: string,
    content: string,
    directive: boolean,
    intent?: ChatIntent,
  ) => {
    const fingerprint = JSON.stringify([id, content, directive, intent]);
    const client_id = pendingDeliveries.get(fingerprint) || crypto.randomUUID();
    pendingDeliveries.set(fingerprint, client_id);
    const message = await request<ChatMessage>(
      `/projects/${id}/messages`,
      json({ content, directive, client_id, intent }),
    );
    pendingDeliveries.delete(fingerprint);
    return message;
  },
  upload: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<InputFile>(`/projects/${id}/files`, {
      method: "POST",
      body,
    });
  },
};
