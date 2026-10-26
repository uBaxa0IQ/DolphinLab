import type {
  Project,
  ChatMessage,
  InputFile,
  WorkspaceSettings,
  ProjectPolicy,
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
export const api = {
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
  }) =>
    request<WorkspaceSettings>("/settings", {
      ...json(settings),
      method: "PUT",
    }),
  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  create: (name: string, description: string) =>
    request<Project>("/projects", json({ name, description })),
  messages: (id: string) => request<ChatMessage[]>(`/projects/${id}/messages`),
  send: (id: string, content: string, directive: boolean) =>
    request<ChatMessage>(
      `/projects/${id}/messages`,
      json({ content, directive }),
    ),
  upload: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<InputFile>(`/projects/${id}/files`, {
      method: "POST",
      body,
    });
  },
};
