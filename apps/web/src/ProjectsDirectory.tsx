import { useState } from "react";
import {
  ArrowUpRight,
  FileText,
  FlaskConical,
  FolderOpen,
  Plus,
  Search,
  Users,
} from "lucide-react";
import type { Project } from "./types";
import { statusLabels } from "./RunPanel";

export default function ProjectsDirectory({
  projects,
  select,
  create,
  name,
}: {
  projects: Project[];
  select: (id: string) => void;
  create: () => void;
  name: string;
}) {
  const [filter, setFilter] = useState<"all" | "personal" | "demo" | "archive">(
    "all",
  );
  const [query, setQuery] = useState("");
  const visible = projects.filter(
    (p) =>
      (filter === "archive"
        ? p.archived
        : !p.archived &&
          (filter === "all" || (filter === "demo" ? p.demo : !p.demo))) &&
      p.name.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div className="projects-directory">
      <div className="directory-heading">
        <div>
          <div className="eyebrow">ЛИЧНОЕ ПРОСТРАНСТВО</div>
          <h1>Проекты</h1>
          <p>
            {name === "Исследователь"
              ? "Все исследования в одном месте."
              : `${name}, здесь твои исследования.`}
          </p>
        </div>
        <button className="button primary" onClick={create}>
          <Plus size={15} />
          Новый проект
        </button>
      </div>
      <div className="directory-tools">
        <div className="directory-filters" role="tablist" aria-label="Проекты">
          <button
            role="tab"
            aria-selected={filter === "all"}
            className={filter === "all" ? "active" : ""}
            onClick={() => setFilter("all")}
          >
            Все <span>{projects.length}</span>
          </button>
          <button
            role="tab"
            aria-selected={filter === "personal"}
            className={filter === "personal" ? "active" : ""}
            onClick={() => setFilter("personal")}
          >
            Мои
          </button>
          <button
            role="tab"
            aria-selected={filter === "demo"}
            className={filter === "demo" ? "active" : ""}
            onClick={() => setFilter("demo")}
          >
            Демо
          </button>
          <button
            role="tab"
            aria-selected={filter === "archive"}
            className={filter === "archive" ? "active" : ""}
            onClick={() => setFilter("archive")}
          >
            Архив
          </button>
        </div>
        <label className="directory-search">
          <Search size={14} />
          <input
            aria-label="Найти проект"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Найти проект…"
          />
        </label>
      </div>
      <div className="directory-grid">
        {visible.map((project) => (
          <button
            key={project.id}
            className="directory-card"
            onClick={() => select(project.id)}
          >
            <div className="directory-card-top">
              <span className="directory-folder">
                <FolderOpen size={22} strokeWidth={1.5} />
              </span>
              <span
                className={`badge ${project.status === "running" ? "green" : project.status === "draft" ? "amber" : "neutral"}`}
              >
                <span className={`tiny-dot ${project.status}`} />
                {project.archived
                  ? "В архиве"
                  : statusLabels[project.status] || project.status}
              </span>
            </div>
            <h2>
              {project.name}
              <ArrowUpRight size={16} />
            </h2>
            <p>
              {project.description ||
                "Добавь описание задачи и исходные данные."}
            </p>
            <div className="directory-card-meta">
              <span>
                <Users size={12} />
                {project.teams.length} команд
              </span>
              <span>
                <FileText size={12} />
                {project.files.length} файлов
              </span>
              {project.demo && <span className="directory-demo">Демо</span>}
            </div>
            <div className="directory-card-result">
              <FlaskConical size={13} />
              <span>
                {project.score
                  ? `${project.metric} · ${project.score}`
                  : "Первое исследование ещё впереди"}
              </span>
              {project.score && <span className="tiny-dot verified" />}
            </div>
          </button>
        ))}
      </div>
      {!visible.length && (
        <div className="empty-state directory-empty">
          <span className="empty-icon">
            <FolderOpen size={30} strokeWidth={1.2} />
          </span>
          <h3>{query ? "Проект не найден" : "Начни своё исследование"}</h3>
          <p>
            {query
              ? "Попробуй другое название."
              : "Добавь задачу и данные — всё остальное останется в проекте."}
          </p>
          {!query && (
            <button className="button secondary" onClick={create}>
              <Plus size={15} />
              Создать проект
            </button>
          )}
        </div>
      )}
    </div>
  );
}
