import { useEffect, useRef, useState } from "react";
import { MessageSquare, Clock3, ArrowDownToLine } from "lucide-react";
import { api } from "../../lib/api";
import { shortText } from "../../lib/presentation";
import Markdown from "../../components/Markdown";
import type { Project } from "../../types";
import type { PageSummary, PageRecord, PageRelease } from "../../types/pages";
import AdvancedPages, { PagePreview } from "./ResearchPages";
import ReportPanel from "./ReportPanel";
import AnalyticalChart from "./AnalyticalChart";

const date = (v: string) =>
  new Date(v).toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

export default function ResearchWorkspace({
  project,
  onDiscuss,
  enabled = false,
}: {
  project: Project;
  onDiscuss: () => void;
  enabled?: boolean;
}) {
  const [pages, setPages] = useState<PageSummary[]>([]),
    [record, setRecord] = useState<PageRecord | null>(null),
    [selected, setSelected] = useState(
      () => new URLSearchParams(location.search).get("page") || "",
    );
  const [release, setRelease] = useState<PageRelease | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(!project.demo),
    [refresh, setRefresh] = useState(0);
  const [tools, setTools] = useState(false),
    [note, setNote] = useState(""),
    [busy, setBusy] = useState(false);
  const [editor, setEditor] =
    useState<Awaited<ReturnType<typeof api.editor>>>(null);
  useEffect(() => {
    if (project.demo) return;
    let active = true;
    const load = () =>
      api
        .editor(project.id)
        .then((v) => {
          if (active) {
            if (v?.state === "ready" && lastEditor.current !== v.id) {
              lastEditor.current = v.id;
              setRefresh((n) => n + 1);
            }
            setEditor(v);
          }
        })
        .catch(() => {});
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [project.id, project.demo]);
  async function compose() {
    if (!project.latest_run) return;
    setBusy(true);
    setError("");
    try {
      await api.composePages(project.id, project.latest_run.id);
      setEditor(await api.editor(project.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const lastEditor = useRef("");
  const chosenPublication = useRef(
    new URLSearchParams(location.search).get("publication") || "",
  );
  function rememberPage(page: string, publication = "") {
    chosenPublication.current = publication;
    const params = new URLSearchParams(location.search);
    params.set("page", page);
    if (publication) params.set("publication", publication);
    else params.delete("publication");
    history.replaceState(null, "", `${location.pathname}?${params}`);
  }
  const noteId = useRef(crypto.randomUUID());
  useEffect(() => {
    if (project.demo) return;
    let active = true;
    api
      .pages(project.id)
      .then((items) => {
        if (!active) return;
        setPages(items);
        setSelected((id) =>
          items.some((p) => p.id === id)
            ? id
            : items.find((p) => p.kind === "main")?.id || items[0]?.id || "",
        );
        setLoading(false);
        setError("");
      })
      .catch((e) => {
        if (active) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [project.id, project.demo, project.latest_run?.updated, refresh]);
  useEffect(() => {
    if (!selected) {
      setRecord(null);
      return;
    }
    let active = true;
    setLoading(true);
    api
      .page(project.id, selected)
      .then(async (value) => {
        const choice = chosenPublication.current;
        const selectedRelease =
          choice === "draft"
            ? null
            : choice && choice !== value.published_id
              ? await api.pagePublication(project.id, value.id, choice)
              : value.publication;
        if (active) {
          setRecord(value);
          setRelease(selectedRelease);
          setLoading(false);
          setError("");
        }
      })
      .catch((e) => {
        if (active) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [project.id, selected, refresh]);
  async function feedback(change: Parameters<typeof api.pageFeedback>[3]) {
    if (!record || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.pageFeedback(
        project.id,
        record.id,
        record.feedback_revision,
        change,
        record.revision,
      );
      setRefresh((n) => n + 1);
      if (change.action === "comment") {
        setNote("");
        noteId.current = crypto.randomUUID();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (project.demo)
    return <DemoResearch project={project} onDiscuss={onDiscuss} />;
  return (
    <section className="research-workspace">
      {project.latest_run?.publication?.candidate_id && (
        <section className="ml-delivery report-feature">
          <span className="eyebrow">РЕЗУЛЬТАТ МОДЕЛИ</span>
          <h2>
            {project.score
              ? `${project.metric || "Метрика"}: ${project.score}`
              : "Результат моделирования готов"}
          </h2>
          <p className="muted">
            Сохранён результат завершённого исследования. Расчёты и проверка
            доступны в истории работы.
          </p>
          <a
            className="button primary"
            href={`/api/projects/${project.id}/runs/${project.latest_run.id}/artifact?path=task%2Fsubmission.csv`}
          >
            <ArrowDownToLine size={15} />
            Скачать результат
          </a>
        </section>
      )}

      {error && (
        <div className="form-error" role="alert">
          Не удалось обновить страницу: {error}{" "}
          <button
            className="text-button"
            onClick={() => setRefresh((n) => n + 1)}
          >
            Повторить
          </button>
        </div>
      )}
      {loading && (
        <p className="muted" role="status">
          Открываем исследование…
        </p>
      )}
      {project.latest_run?.publication?.report_id &&
        editor?.state !== "ready" && (
          <div className="editor-state">
            <span>
              {editor?.state === "processing" || editor?.state === "queued"
                ? "Редактор собирает исследование…"
                : editor?.state === "failed" || editor?.state === "conflict"
                  ? editor.error
                  : editor?.state === "ready"
                    ? "Страницы подготовлены редактором"
                    : "Результаты готовы. Редактор может оформить страницы исследования."}
            </span>
            <button
              className="text-button"
              disabled={
                !enabled ||
                busy ||
                ["queued", "processing"].includes(editor?.state || "")
              }
              onClick={() => void compose()}
            >
              {editor?.state === "ready"
                ? "Обновить оформление"
                : "Подготовить страницы"}
            </button>
          </div>
        )}
      {!!pages.length && (
        <nav className="report-navigation" aria-label="Страницы исследования">
          {pages.map((p) => (
            <button
              key={p.id}
              className={selected === p.id ? "active" : ""}
              onClick={() => {
                rememberPage(p.id);
                setSelected(p.id);
              }}
            >
              {p.kind === "main" ? "Общая картина" : shortText(p.title, 38)}
            </button>
          ))}
        </nav>
      )}
      {record && (
        <>
          <div className="reader-tools">
            <span className="reader-byline">
              {release
                ? `${release.author?.kind === "editor" ? "Редактор" : "Публикация"} · ${date(release.created)}`
                : "Черновик страницы"}
            </span>
            <div>
              {record.history.length > 0 && (
                <label className="history-select">
                  <Clock3 size={14} />
                  <select
                    aria-label="История страницы"
                    value={release?.id || "draft"}
                    disabled={busy}
                    onChange={async (e) => {
                      const id = e.target.value;
                      rememberPage(record.id, id === record.published_id ? "" : id);
                      setBusy(true);
                      try {
                        setRelease(
                          id === "draft"
                            ? null
                            : await api.pagePublication(
                                project.id,
                                record.id,
                                id,
                              ),
                        );
                      } catch (e) {
                        setError((e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    {record.history.map((h, i) => (
                      <option key={h.id} value={h.id}>
                        {i === 0 ? "Последняя публикация" : date(h.created)}
                      </option>
                    ))}
                    <option value="draft">Черновик редактора</option>
                  </select>
                </label>
              )}
              {release && (
                <a
                  className="icon-button"
                  title="Экспорт исследования"
                  aria-label="Экспорт исследования"
                  href={`/api/projects/${project.id}/pages/${record.id}/export?release_id=${release.id}`}
                >
                  <ArrowDownToLine size={16} />
                </a>
              )}
            </div>
          </div>
          <PagePreview
            projectId={project.id}
            page={release?.page || record.draft.page}
            release={release}
            showPublicationMeta={false}
            pins={record.user_state.pins}
            canPin={(block) =>
              record.draft.page.blocks.some(
                (b) => JSON.stringify(b) === JSON.stringify(block),
              )
            }
            onPin={
              !release || release.id === record.published_id
                ? (id, pinned) =>
                    void feedback({
                      action: pinned ? "unpin" : "pin",
                      block_id: id,
                    })
                : undefined
            }
          />
          <div className="research-next">
            <div>
              <span className="eyebrow">ПРОДОЛЖИМ ИССЛЕДОВАНИЕ?</span>
              <h3>Какой вопрос проверим дальше?</h3>
              <p>
                Обсудите выводы с помощником или оставьте заметку к странице.
              </p>
            </div>
            <button className="button primary" onClick={onDiscuss}>
              <MessageSquare size={15} />
              Обсудить результат
            </button>
          </div>
          <details className="page-notes">
            <summary>
              Ваши заметки{" "}
              {Object.keys(record.user_state.comments).length
                ? `· ${Object.keys(record.user_state.comments).length}`
                : ""}
            </summary>
            {Object.values(record.user_state.comments).map((c) => (
              <div className="reader-note" key={c.id}>
                <Markdown text={c.text} />
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() =>
                    void feedback({
                      action: "delete_comment",
                      comment_id: c.id,
                    })
                  }
                >
                  Убрать заметку
                </button>
              </div>
            ))}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void feedback({
                  action: "comment",
                  comment_id: noteId.current,
                  text: note,
                });
              }}
            >
              <textarea
                aria-label="Заметка к странице"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Что важно сохранить или уточнить?"
                maxLength={4000}
              />
              <button
                className="button secondary small"
                disabled={busy || !note.trim()}
              >
                Сохранить заметку
              </button>
            </form>
          </details>
        </>
      )}
      {!loading && !record && (
        <>
          {project.latest_run?.publication ? (
            <ReportPanel project={project} />
          ) : (
            <div className="research-welcome">
              <span className="welcome-mark">✳</span>
              <h2>У каждого исследования есть первый вопрос</h2>
              <p>
                Добавьте материалы и расскажите, что хотите узнать. Команда
                проведёт анализ, а редактор соберёт выводы, графики и таблицы в
                этой области.
              </p>
              <button className="button primary" onClick={onDiscuss}>
                Обсудить задачу
              </button>
            </div>
          )}
        </>
      )}
      <details
        className="technical-details developer-tools"
        onToggle={(e) => setTools(e.currentTarget.open)}
      >
        <summary>Инструменты разработчика</summary>
        <p className="muted">
          Ручная компоновка и технические снимки. Обычно страницы собирает
          редактор.
        </p>
        {tools && <AdvancedPages project={project} />}
      </details>
    </section>
  );
}

function DemoResearch({
  project,
  onDiscuss,
}: {
  project: Project;
  onDiscuss: () => void;
}) {
  const forecast = project.id === "demo-demand";
  return (
    <article className="demo-research">
      <div className="report-hero">
        <span className="eyebrow">ОБЩАЯ КАРТИНА</span>
        <h2>
          {forecast
            ? "Спрос стал предсказуемее"
            : "Что помогает предсказать отток"}
        </h2>
        <p>
          {forecast
            ? "Модель учитывает сезонность и календарные особенности. Следующий шаг — проверить её на новом периоде."
            : "Поведенческие признаки улучшили качество прогноза. Теперь важно проверить устойчивость результата на новых данных."}
        </p>
      </div>
      <div className="editor-metrics">
        <section>
          <span>{forecast ? "Средняя ошибка" : "Качество прогноза"}</span>
          <strong>{project.score || "0.8914"}</strong>
          <small>
            {forecast
              ? "MAE на проверочной выборке"
              : "ROC AUC · проверочная выборка"}
          </small>
        </section>
        <section>
          <span>Статус результата</span>
          <strong className="metric-word">Проверен</strong>
          <small>Независимая проверка</small>
        </section>
      </div>
      <section className="report-feature">
        <div className="section-heading">
          <div>
            <span className="eyebrow">СРАВНЕНИЕ ПОДХОДОВ</span>
            <h3>
              {forecast
                ? "Ошибка прогноза снижается"
                : "Новые признаки дают улучшение"}
            </h3>
          </div>
        </div>
        <AnalyticalChart
          spec={{
            type: "bar",
            table_id: "demo",
            x: "approach",
            y: "value",
            x_label: "Подход",
            y_label: forecast ? "MAE" : "ROC AUC",
            caption: "Вымышленные данные демонстрационного проекта",
          }}
          table={{
            columns: [
              { name: "approach", label: "Подход", type: "string", unit: "" },
              { name: "value", label: "Результат", type: "number", unit: "" },
            ],
            rows: forecast
              ? [
                  ["Базовый", 18.6],
                  ["Сезонность", 15.2],
                  ["Итоговый", 12.48],
                ]
              : [
                  ["Базовый", 0.81],
                  ["Исходные признаки", 0.8732],
                  ["Поведение", 0.8914],
                ],
            selection: "Демонстрация",
            sample_size: 0,
          }}
        />
      </section>
      <div className="research-next">
        <div>
          <h3>Что проверим следующим?</h3>
          <p>Уточните гипотезу или задайте вопрос о результатах.</p>
        </div>
        <button className="button primary" onClick={onDiscuss}>
          Обсудить с помощником
        </button>
      </div>
    </article>
  );
}
