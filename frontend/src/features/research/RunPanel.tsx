import {
  ArrowDownToLine,
  CheckCircle2,
  Clock3,
  FileText,
  History,
  Loader2,
  Pause,
  Play,
  Square,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { Project, ResearchRun } from "../../types";

export const statusLabels: Record<string, string> = {
  draft: "Черновик",
  queued: "В очереди",
  running: "В работе",
  recovering: "Восстановление",
  pausing: "Приостанавливается",
  paused: "На паузе",
  finalizing: "Подготовка результата",
  stopping: "Останавливается",
  stopped: "Остановлен",
  completed: "Завершён",
  failed: "Ошибка",
};
const terminal = new Set(["completed", "stopped", "failed"]);

export default function RunPanel({
  project,
  enabled,
  history,
  changed,
  openHistory,
}: {
  project: Project;
  enabled: boolean;
  history: boolean;
  changed: () => void;
  openHistory: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const [selected, setSelected] = useState<ResearchRun | null>(null);
  const [files, setFiles] = useState<{ path: string; size: number }[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const run = project.latest_run;
  useEffect(() => {
    let active = true;
    setError("");
    if (history && !project.demo)
      api
        .runs(project.id)
        .then((items) => {
          if (active) setRuns(items);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    return () => {
      active = false;
    };
  }, [history, project.id, run?.updated]);
  async function action(name: string) {
    if (busy) return;
    if (
      name === "stop" &&
      !window.confirm(
        "Остановить активные задания? Сохранённые результаты останутся доступны.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      if (project.demo) {
        setError(
          "Это демопроект. Для реального исследования создай свой проект.",
        );
        return;
      }
      if (name === "start") await api.start(project.id, undefined, run?.id);
      else if (run) await api.control(project.id, run.id, name);
      changed();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function artifacts(item: ResearchRun) {
    setSelected(item);
    setFiles([]);
    setFilesLoading(true);
    try {
      setFiles(await api.artifacts(project.id, item.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setFilesLoading(false);
    }
  }
  return (
    <section className="run-panel">
      <div className="run-toolbar">
        <div className="run-toolbar-state">
          <span className={`tiny-dot ${project.status}`} />
          <strong>
            {project.demo
              ? "Демонстрация"
              : run
                ? statusLabels[run.status]
                : "Готов к подготовке"}
          </strong>
          {run && (
            <span>
              {Math.floor(run.elapsed_seconds / 60)} мин активного времени
            </span>
          )}
        </div>
        <div className="run-toolbar-actions">
          {!project.demo && (!run || terminal.has(run.status)) && (
            <span
              title={
                !enabled
                  ? "Включи ENABLE_MODEL_CALLS в Docker, чтобы разрешить запросы к модели"
                  : undefined
              }
            >
              <button
                className="button primary small"
                disabled={busy || !enabled || project.archived}
                onClick={() => void action("start")}
              >
                {busy ? (
                  <Loader2 size={13} className="spin" />
                ) : (
                  <Play size={13} />
                )}{" "}
                {run ? "Новый запуск" : "Начать исследование"}
              </button>
            </span>
          )}
          {run && ["running", "recovering"].includes(run.status) && (
            <>
              <button
                className="button secondary small"
                disabled={busy}
                onClick={() => void action("pause")}
              >
                <Pause size={12} />
                Пауза
              </button>
              <button
                className="button secondary small"
                disabled={busy}
                onClick={() => void action("finish")}
              >
                <CheckCircle2 size={12} />
                Завершить
              </button>
            </>
          )}
          {run?.status === "paused" && (
            <button
              className="button primary small"
              disabled={busy || !enabled}
              onClick={() => void action("resume")}
            >
              <Play size={12} />
              Продолжить
            </button>
          )}
          {run && !terminal.has(run.status) && (
            <button
              className="run-stop"
              title="Остановить задания"
              aria-label="Остановить задания"
              disabled={busy}
              onClick={() => void action("stop")}
            >
              <Square size={13} />
            </button>
          )}
          <button
            className="icon-button"
            title="История запусков"
            aria-label="История запусков"
            onClick={openHistory}
          >
            <History size={16} />
          </button>
        </div>
      </div>
      {!project.demo && project.archived && (
        <p className="run-offline">
          Проект в архиве. Верни его через меню управления, чтобы продолжить
          работу.
        </p>
      )}
      {!project.demo && !project.archived && !enabled && (
        <p className="run-offline">
          Вызовы модели отключены в Docker. Проекты, файлы и настройки
          сохраняются.
        </p>
      )}
      {run?.usage?.requests !== undefined && (
        <div className="run-usage">
          <span>{run.usage.requests} запросов</span>
          <span>
            {(
              (run.usage.prompt_tokens || 0) +
              (run.usage.completion_tokens || 0)
            ).toLocaleString("ru-RU")}{" "}
            токенов
          </span>
          <span>
            {run.usage.currency === "unavailable"
              ? "Стоимость недоступна"
              : `${(run.usage.accounted_cost || 0).toFixed(3)} ${run.usage.currency || ""}`}
          </span>
          {!!run.usage.reserved_cost && (
            <span>Резерв: {run.usage.reserved_cost.toFixed(3)}</span>
          )}
        </div>
      )}
      {run?.error && <p className="form-error">{run.error}</p>}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {history && (
        <div className="run-history">
          <div className="section-heading">
            <h2>История запусков</h2>
          </div>
          {project.demo ? (
            <div className="quiet-empty">
              <History size={19} />
              <span>
                Вымышленные результаты доступны во вкладке «Эксперименты».
              </span>
            </div>
          ) : runs.length ? (
            runs.map((item) => (
              <div className="run-history-row" key={item.id}>
                <span className="run-id">{item.id.slice(0, 8)}</span>
                <div>
                  <strong>{statusLabels[item.status]}</strong>
                  <small>
                    {new Date(item.created).toLocaleString("ru-RU")}
                  </small>
                </div>
                <span>{Math.floor(item.elapsed_seconds / 60)} мин</span>
                <button
                  className="button secondary small"
                  onClick={() => void artifacts(item)}
                >
                  <FileText size={12} />
                  Результаты и логи
                </button>
              </div>
            ))
          ) : (
            <div className="quiet-empty">
              <Clock3 size={19} />
              <span>Пока нет запусков</span>
            </div>
          )}
        </div>
      )}
      {selected && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSelected(null);
          }}
        >
          <section
            className="modal wide"
            role="dialog"
            aria-modal="true"
            aria-label="Артефакты запуска"
          >
            <button
              className="icon-button modal-close"
              onClick={() => setSelected(null)}
              aria-label="Закрыть"
            >
              <X size={18} />
            </button>
            <span className="eyebrow">ЗАПУСК {selected.id.slice(0, 8)}</span>
            <h2>Результаты и логи</h2>
            <p className="muted artifact-caption">
              Файлы этого запуска. Проверенный результат отмечен отдельно.
            </p>
            {selected.publication && (
              <a
                className="button primary artifact-download"
                href={`/api/projects/${project.id}/runs/${selected.id}/artifact?path=${encodeURIComponent("task/submission.csv")}`}
              >
                <CheckCircle2 size={15} />
                Проверенный submission.csv
                <ArrowDownToLine size={14} />
              </a>
            )}
            {filesLoading ? (
              <div className="settings-loading">
                <Loader2 className="spin" size={20} />
              </div>
            ) : (
              <div className="artifact-list">
                {files.length ? (
                  files.map((file) => (
                    <a
                      key={file.path}
                      href={`/api/projects/${project.id}/runs/${selected.id}/artifact?path=${encodeURIComponent(file.path)}`}
                    >
                      <FileText size={13} />
                      <span>{file.path}</span>
                      <small>
                        {Math.max(1, Math.round(file.size / 1024))} КБ
                      </small>
                      <ArrowDownToLine size={13} />
                    </a>
                  ))
                ) : (
                  <p className="muted">Артефактов пока нет.</p>
                )}
              </div>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
