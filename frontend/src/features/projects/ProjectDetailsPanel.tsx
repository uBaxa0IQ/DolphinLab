import { Archive, Check, Loader2, Trash2, Undo2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api } from "../../lib/api";
import type { Project } from "../../types";

export default function ProjectDetailsPanel({
  project,
  saved,
  deleted,
  close,
}: {
  project: Project;
  saved: (p: Project) => void;
  deleted: (removed: boolean) => void;
  close: () => void;
}) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function update(event?: FormEvent, archive = !!project.archived) {
    event?.preventDefault();
    if (busy || project.demo || !name.trim()) return;
    setBusy(true);
    setError("");
    try {
      saved(
        await api.editProject(project.id, {
          name: name.trim(),
          description,
          archived: archive,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (
      busy ||
      project.demo ||
      !window.confirm(
        "Удалить проект, переписку, файлы и всю историю исследований? Это действие нельзя отменить.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      const result = await api.deleteProject(project.id);
      deleted(result.files_removed);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="settings-panel">
      <span className="eyebrow">ПРОЕКТ</span>
      <h2>Задача и управление</h2>
      <form onSubmit={update}>
        <label className="field-label">
          Название
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
            required
            disabled={project.demo}
          />
        </label>
        <label className="field-label">
          Задача
          <textarea
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={20000}
            disabled={project.demo}
          />
        </label>
        <p className="field-help">
          {project.demo
            ? "Это демонстрационный проект. Создай свой, чтобы менять задачу."
            : "Изменения задачи применяются к следующим запускам. История сохраняется."}
        </p>
        {!project.demo && (
          <div className="project-management">
            <button
              type="button"
              disabled={busy}
              onClick={() => void update(undefined, !project.archived)}
            >
              {project.archived ? <Undo2 size={15} /> : <Archive size={15} />}{" "}
              {project.archived ? "Вернуть из архива" : "В архив"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void remove()}
              className="delete-project"
            >
              <Trash2 size={15} />
              Удалить проект
            </button>
          </div>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button ghost" onClick={close}>
            Закрыть
          </button>
          <button
            className="button primary"
            type="submit"
            disabled={busy || project.demo || !name.trim()}
          >
            {busy ? (
              <Loader2 size={15} className="spin" />
            ) : (
              <Check size={15} />
            )}
            Сохранить
          </button>
        </div>
      </form>
    </div>
  );
}
