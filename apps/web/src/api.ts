import type {
  Project,
  ChatMessage,
  InputFile,
  WorkspaceSettings,
  ProjectPolicy,
  ResearchRun,
} from "./types";
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
export const api = {
  runs: (id: string) => request<ResearchRun[]>(`/projects/${id}/runs`),
  start: (id: string) => request<ResearchRun>(`/projects/${id}/runs`, json({})),
  control: (id: string, run: string, action: string) =>
    request<ResearchRun>(
      `/projects/${id}/runs/${run}/control`,
      json({ action }),
    ),
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
    data: { name: string; description: string; archived: boolean },
  ) => request<Project>(`/projects/${id}`, { ...json(data), method: "PATCH" }),
  deleteProject: (id: string) =>
    request<{ deleted: boolean; files_removed: boolean }>(`/projects/${id}`, {
      method: "DELETE",
    }),
  project: (id: string) => request<Project>(`/projects/${id}`),
  create: (name: string, description: string) =>
    request<Project>("/projects", json({ name, description })),
  messages: (id: string) => request<ChatMessage[]>(`/projects/${id}/messages`),
  send: async (id: string, content: string, directive: boolean) => {
    const fingerprint = JSON.stringify([id, content, directive]);
    const client_id = pendingDeliveries.get(fingerprint) || crypto.randomUUID();
    pendingDeliveries.set(fingerprint, client_id);
    const message = await request<ChatMessage>(
      `/projects/${id}/messages`,
      json({ content, directive, client_id }),
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
