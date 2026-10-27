import { FileText, FolderOpen, Loader2, Plus, Upload, X } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import Modal from "../../components/Modal";
import { api } from "../../lib/api";
import { size } from "../../lib/format";
import type { Project } from "../../types";

export default function CreateProject({
  close,
  created,
}: {
  close: () => void;
  created: (project: Project, warning?: string) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const addFiles = (items: File[]) =>
    setFiles((before) => [
      ...before,
      ...items.filter(
        (item) =>
          !before.some((f) => f.name === item.name && f.size === item.size),
      ),
    ]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    let project: Project;
    try {
      project = await api.create(name.trim(), description.trim());
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
      return;
    }
    const failures: string[] = [];
    for (const file of files) {
      try {
        project.files.push(await api.upload(project.id, file));
      } catch {
        failures.push(file.name);
      }
    }
    created(
      project,
      failures.length
        ? `Проект сохранён. Не удалось загрузить: ${failures.join(", ")}. Повтори загрузку во вкладке «Файлы».`
        : undefined,
    );
  }
  return (
    <Modal close={close} label="Новый проект">
      <div className="modal-symbol">
        <FolderPlusIcon />
      </div>
      <h2>Новое исследование</h2>
      <p className="muted modal-intro">
        Задача, данные и пространство для команды.
      </p>
      <form onSubmit={submit}>
        <label className="field-label">
          Название
          <input
            autoFocus
            required
            maxLength={120}
            placeholder="Например, прогноз спроса"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="field-label">
          Задача
          <textarea
            rows={3}
            placeholder="Что нужно получить? Какие ограничения важны?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={20000}
          />
        </label>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            addFiles(Array.from(e.target.files || []));
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className={`dropzone ${drag ? "dragging" : ""}`}
          onClick={() => picker.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            addFiles(Array.from(e.dataTransfer.files));
          }}
        >
          <Upload size={23} />
          <span>
            Перетащи файлы или <b>выбери на компьютере</b>
          </span>
          <small>Датасет, task.md, пример результата · до 100 МБ на файл</small>
        </button>
        {files.length > 0 && (
          <ul className="upload-list">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`}>
                <FileText size={16} />
                <span>{f.name}</span>
                <small>{size(f.size)}</small>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Убрать ${f.name}`}
                  onClick={() =>
                    setFiles((before) =>
                      before.filter((_, index) => index !== i),
                    )
                  }
                >
                  <X size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button
            type="button"
            className="button ghost"
            onClick={close}
            disabled={busy}
          >
            Отмена
          </button>
          <button
            className="button primary"
            type="submit"
            disabled={!name.trim() || busy}
          >
            {busy ? <Loader2 size={16} className="spin" /> : <Plus size={16} />}
            {busy ? "Сохраняем…" : "Создать проект"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function FolderPlusIcon() {
  return <FolderOpen size={25} strokeWidth={1.5} />;
}
