import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { Project } from "../../types";
import type { ResultIndex } from "../../types/reports";
import type {
  PageBlock,
  PageCatalog,
  PageRecord,
  PageRelease,
  PageSpec,
  PageSummary,
} from "../../types/pages";
import ReportPanel, { BlockBoundary, ResultBlock } from "./ReportPanel";
import "./ResearchPages.css";

const blank = (): PageSpec => ({
  schema_version: 1,
  title: "Общая страница проекта",
  blocks: [],
});
const date = (value: string) =>
  new Date(value).toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

export function ProjectPageOverview({
  project,
  onEdit,
}: {
  project: Project;
  onEdit: () => void;
}) {
  const [page, setPage] = useState<PageRecord | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .pages(project.id)
      .then(async (pages) => {
        const main = pages.find((p) => p.kind === "main" && p.published_id);
        const value = main ? await api.page(project.id, main.id) : null;
        if (active) {
          setPage(value);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [project.id, project.latest_run?.updated]);
  return (
    <>
      {error && (
        <p className="form-error" role="alert">
          Не удалось обновить общую страницу: {error}
        </p>
      )}
      {page?.publication ? (
        <section className="research-pages" aria-label="Общая страница проекта">
          <div className="page-toolbar">
            <span className="eyebrow">ОБЩАЯ СТРАНИЦА ПРОЕКТА</span>
            <button className="button secondary small" onClick={onEdit}>
              Страницы и история
            </button>
          </div>
          <PagePreview
            projectId={project.id}
            page={page.publication.page}
            release={page.publication}
            refresh={project.latest_run?.updated}
          />
        </section>
      ) : (
        <ReportPanel project={project} />
      )}
    </>
  );
}

export function PagePreview({
  projectId,
  page,
  release,
  filter = "",
  refresh = "",
}: {
  projectId: string;
  page: PageSpec;
  release?: PageRelease | null;
  filter?: string;
  refresh?: string;
}) {
  return (
    <div className="research-page-preview">
      <h2>{page.title}</h2>
      {release ? (
        <p className="muted">
          Опубликована · версия {release.revision} · {date(release.created)}
        </p>
      ) : (
        <p className="page-draft-label">
          Предпросмотр черновика · не опубликован
        </p>
      )}
      {release?.current_evidence.needs_review && (
        <p className="form-error" role="status">
          Проверка обязательных доказательств изменилась. Публикация сохранена в
          истории и требует пересмотра.
        </p>
      )}
      <div className="research-page-grid">
        {page.blocks.map((block) => (
          <section
            key={block.id}
            className={`research-page-cell ${block.width}`}
          >
            {block.caption && (
              <p className="page-editor-caption">
                <span>Подпись редактора</span>
                {block.caption}
              </p>
            )}
            {!block.required && (
              <p className="muted">
                Дополнительный материал · статус проверки указан у результата
              </p>
            )}
            <BlockBoundary
              key={`${block.run_id}:${block.result_id}:${block.sha256}`}
            >
              <ResultBlock
                projectId={projectId}
                runId={block.run_id}
                id={block.result_id}
                filterText={filter || block.filter_text}
                refresh={refresh}
              />
            </BlockBoundary>
            {release && (
              <details className="page-provenance">
                <summary>Источник и состояние на момент публикации</summary>
                <p>Запуск: {block.run_id}</p>
                <p>
                  Результат: {block.result_id} · SHA256 {block.sha256}
                </p>
                <p>
                  При публикации:{" "}
                  {release.manifest.sources.find(
                    (s) =>
                      s.run_id === block.run_id &&
                      s.result_id === block.result_id,
                  )?.status || "недоступен"}
                  . Текущий статус показан в блоке.
                </p>
              </details>
            )}
          </section>
        ))}
      </div>
      {!page.blocks.length && (
        <p className="muted">
          Добавьте сохранённые результаты в черновик страницы.
        </p>
      )}
    </div>
  );
}

export default function ResearchPages({ project }: { project: Project }) {
  const [index, setIndex] = useState<PageSummary[]>([]),
    [catalog, setCatalog] = useState<PageCatalog>({ runs: [], teams: [] });
  const [selected, setSelected] = useState(""),
    [record, setRecord] = useState<PageRecord | null>(null);
  const [draft, setDraft] = useState<PageSpec>(blank),
    [editing, setEditing] = useState(false),
    [dirty, setDirty] = useState(false);
  const [view, setView] = useState("draft"),
    [historical, setHistorical] = useState<PageRelease | null>(null);
  const [runId, setRunId] = useState(""),
    [resultIndex, setResultIndex] = useState<ResultIndex | null>(null),
    [chosenResult, setChosenResult] = useState("");
  const [createKind, setCreateKind] = useState("main"),
    [teamId, setTeamId] = useState(""),
    [compareRun, setCompareRun] = useState("");
  const [filter, setFilter] = useState(""),
    [comment, setComment] = useState(""),
    [outcome, setOutcome] = useState<"complete" | "inconclusive">("complete");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [refresh, setRefresh] = useState(0);
  const commentId = useRef(crypto.randomUUID());
  useEffect(() => {
    let active = true;
    Promise.all([api.pages(project.id), api.pageCatalog(project.id)])
      .then(([pages, sources]) => {
        if (!active) return;
        setIndex(pages);
        setCatalog(sources);
        setSelected(
          (id) =>
            id ||
            pages.find((p) => p.kind === "main")?.id ||
            pages[0]?.id ||
            "",
        );
        setRunId((id) => id || sources.runs[0]?.id || "");
        setLoading(false);
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
  }, [project.id, refresh]);
  useEffect(() => {
    let active = true;
    if (!selected) {
      setRecord(null);
      return;
    }
    api
      .page(project.id, selected)
      .then((value) => {
        if (!active) return;
        setRecord(value);
        setDraft(value.draft.page);
        setView(value.publication ? "published" : "draft");
        setHistorical(null);
        setEditing(false);
        setDirty(false);
        setError("");
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [project.id, selected, refresh]);
  useEffect(() => {
    let active = true;
    setResultIndex(null);
    setChosenResult("");
    if (runId)
      api
        .results(project.id, runId)
        .then((value) => {
          if (active) setResultIndex(value);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    return () => {
      active = false;
    };
  }, [project.id, runId, refresh]);
  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      setRefresh((x) => x + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  function reload() {
    if (
      dirty &&
      !window.confirm(
        "Обновить страницу и заменить несохранённую компоновку актуальным черновиком?",
      )
    )
      return;
    setRefresh((x) => x + 1);
  }
  function update(page: PageSpec) {
    setDraft(page);
    setDirty(true);
  }
  function changeBlock(id: string, change: Partial<PageBlock>) {
    update({
      ...draft,
      blocks: draft.blocks.map((b) => (b.id === id ? { ...b, ...change } : b)),
    });
  }
  function move(id: string, step: number) {
    const items = [...draft.blocks],
      i = items.findIndex((b) => b.id === id),
      next = i + step;
    if (next < 0 || next >= items.length) return;
    [items[i], items[next]] = [items[next], items[i]];
    update({ ...draft, blocks: items });
  }
  async function save() {
    const owner = record
      ? { kind: record.kind, run_id: record.run_id, team_id: record.team_id }
      : {
          kind: createKind as "main" | "team",
          run_id: createKind === "team" ? runId : null,
          team_id: createKind === "team" ? teamId : null,
        };
    await act(async () => {
      const saved = await api.savePage(
        project.id,
        record?.revision || 0,
        draft,
        owner,
      );
      setSelected(saved.page_id);
      setEditing(false);
      setDirty(false);
    });
  }
  async function publish(revisionId: string) {
    if (!record) return;
    await act(() =>
      api.publishPage(
        project.id,
        record.id,
        record.revision,
        record.feedback_revision,
        revisionId,
        outcome,
      ),
    );
  }
  async function feedback(change: Parameters<typeof api.pageFeedback>[3]) {
    if (!record) return;
    await act(() =>
      api.pageFeedback(project.id, record.id, record.feedback_revision, change),
    );
  }
  function beginNew() {
    if (
      dirty &&
      !window.confirm("Открыть новый черновик без сохранения текущих правок?")
    )
      return;
    setSelected("");
    setRecord(null);
    setCreateKind(index.some((p) => p.kind === "main") ? "team" : "main");
    setTeamId("");
    setDraft(blank());
    setEditing(true);
    setDirty(false);
    setView("draft");
  }
  async function chooseView(value: string) {
    setView(value);
    setHistorical(null);
    if (value !== "draft" && value !== "published" && record) {
      setBusy(true);
      try {
        setHistorical(await api.pagePublication(project.id, record.id, value));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    }
  }
  const release = view === "published" ? record?.publication : historical;
  const shown = editing
    ? draft
    : view === "draft"
      ? record?.draft.page
      : release?.page;
  const comparison = catalog.runs.find((r) => r.id === compareRun),
    sourceRun = catalog.runs.find((r) => r.id === runId);
  const sameConditions =
    comparison &&
    sourceRun &&
    JSON.stringify([
      comparison.inputs,
      comparison.definitions,
      comparison.document_sources,
    ]) ===
      JSON.stringify([
        sourceRun.inputs,
        sourceRun.definitions,
        sourceRun.document_sources,
      ]);
  return (
    <section className="research-pages">
      <div className="section-heading">
        <div>
          <h2>Исследовательские страницы</h2>
          <p className="muted">
            Соберите результаты в общую страницу. Её публикация не завершает
            исследование и не меняет статус проверки результатов.
          </p>
        </div>
        <button
          className="button secondary small"
          onClick={reload}
          disabled={busy}
        >
          Обновить
        </button>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error} Локальный черновик сохранён на экране. При конфликте обновите
          актуальную версию.
        </p>
      )}
      {loading && <p role="status">Загружаем страницы…</p>}
      <div className="page-toolbar">
        <label>
          Страница
          <select
            aria-label="Исследовательская страница"
            value={selected}
            disabled={editing && dirty}
            onChange={(e) => {
              setEditing(false);
              setSelected(e.target.value);
            }}
          >
            <option value="">Новая страница</option>
            {index.map((p) => (
              <option key={p.id} value={p.id}>
                {p.kind === "main" ? "Общая" : "Команда"} · {p.title}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button secondary small"
          disabled={busy}
          onClick={beginNew}
        >
          Новая страница
        </button>
        {record && !editing && (
          <button
            className="button secondary small"
            onClick={() => {
              setDraft(record.draft.page);
              setEditing(true);
              setView("draft");
            }}
          >
            Изменить компоновку
          </button>
        )}
      </div>
      {!index.length && !editing && !loading && (
        <p className="page-empty">
          Общая страница ещё не собрана. Создайте черновик из сохранённых
          результатов или поручите сборку редактору.
        </p>
      )}
      {editing && (
        <div className="page-editor">
          <h3>
            Черновик {record ? `· версия ${record.revision}` : "новой страницы"}
          </h3>
          {!record && (
            <div className="page-toolbar">
              <label>
                Назначение
                <select
                  aria-label="Назначение страницы"
                  value={createKind}
                  onChange={(e) => setCreateKind(e.target.value)}
                >
                  <option
                    value="main"
                    disabled={index.some((p) => p.kind === "main")}
                  >
                    Общая страница проекта
                  </option>
                  <option value="team">Подстраница команды</option>
                </select>
              </label>
              {createKind === "team" && (
                <label>
                  Команда
                  <select
                    aria-label="Команда страницы"
                    value={teamId}
                    onChange={(e) => setTeamId(e.target.value)}
                  >
                    <option value="">
                      Выберите команду выбранного запуска
                    </option>
                    {catalog.teams
                      .filter(
                        (t) =>
                          t.run_id === runId &&
                          !index.some(
                            (p) =>
                              p.kind === "team" &&
                              p.run_id === t.run_id &&
                              p.team_id === t.id,
                          ),
                      )
                      .map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                  </select>
                </label>
              )}
            </div>
          )}
          <label>
            Название
            <input
              aria-label="Название страницы"
              value={draft.title}
              maxLength={200}
              onChange={(e) => update({ ...draft, title: e.target.value })}
            />
          </label>
          <div className="page-toolbar">
            <label>
              Источник результатов
              <select
                aria-label="Запуск источника страницы"
                value={runId}
                onChange={(e) => {
                  setRunId(e.target.value);
                  setTeamId("");
                }}
              >
                <option value="">Выберите запуск</option>
                {catalog.runs.map((r) => (
                  <option value={r.id} key={r.id}>
                    {date(r.created)} · {r.question.slice(0, 80)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Результат
              <select
                aria-label="Результат для блока"
                value={chosenResult}
                onChange={(e) => setChosenResult(e.target.value)}
              >
                <option value="">Выберите результат</option>
                {resultIndex?.results
                  .filter((r) => !r.legacy)
                  .map((r) => (
                    <option value={r.id} key={r.id}>
                      {r.title} ·{" "}
                      {(
                        {
                          verified: "проверено",
                          reported: "не проверено",
                          refuted: "опровергнуто",
                          inconclusive: "недостаточно оснований",
                          broken: "доказательство недоступно",
                        } as Record<string, string>
                      )[r.status || ""] || "статус доступен в блоке"}
                    </option>
                  ))}
              </select>
            </label>
            <button
              className="button secondary small"
              disabled={!chosenResult || draft.blocks.length >= 20}
              onClick={() => {
                const item = resultIndex?.results.find(
                  (r) => r.id === chosenResult,
                );
                if (item)
                  update({
                    ...draft,
                    blocks: [
                      ...draft.blocks,
                      {
                        id: crypto.randomUUID(),
                        run_id: runId,
                        result_id: item.id,
                        sha256: item.sha256,
                        caption: "",
                        width: "full",
                        required: true,
                        filter_text: "",
                        origin_revision_id: null,
                      },
                    ],
                  });
              }}
            >
              Добавить блок
            </button>
          </div>
          {!!index.filter((p) => p.kind === "team" && p.id !== selected)
            .length && (
            <label>
              Добавить материалы команды
              <select
                aria-label="Добавить подстраницу команды"
                defaultValue=""
                onChange={async (e) => {
                  const id = e.target.value;
                  e.target.value = "";
                  if (!id) return;
                  try {
                    const team = await api.page(project.id, id);
                    const revision = team.publication
                      ? {
                          id: team.publication.revision_id,
                          page: team.publication.page,
                        }
                      : team.draft;
                    if (
                      draft.blocks.length + revision.page.blocks.length >
                      20
                    ) {
                      setError("На странице допускается до 20 блоков");
                      return;
                    }
                    update({
                      ...draft,
                      blocks: [
                        ...draft.blocks,
                        ...revision.page.blocks.map((b) => ({
                          ...b,
                          id: crypto.randomUUID(),
                          origin_revision_id: revision.id,
                        })),
                      ],
                    });
                  } catch (reason) {
                    setError(
                      reason instanceof Error ? reason.message : String(reason),
                    );
                  }
                }}
              >
                <option value="">Выберите страницу команды</option>
                {index
                  .filter((p) => p.kind === "team" && p.id !== selected)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.title}
                    </option>
                  ))}
              </select>
            </label>
          )}
          <ol className="page-edit-blocks">
            {draft.blocks.map((block, i) => {
              const pinned = !!record?.user_state.pins[block.id];
              return (
                <li key={block.id}>
                  <div className="page-toolbar">
                    <strong>
                      Блок {i + 1}
                      {pinned ? " · закреплён пользователем" : ""}
                    </strong>
                    <button
                      aria-label={`Поднять блок ${i + 1}`}
                      disabled={i === 0}
                      onClick={() => move(block.id, -1)}
                    >
                      ↑
                    </button>
                    <button
                      aria-label={`Опустить блок ${i + 1}`}
                      disabled={i === draft.blocks.length - 1}
                      onClick={() => move(block.id, 1)}
                    >
                      ↓
                    </button>
                    <button
                      disabled={pinned}
                      onClick={() =>
                        update({
                          ...draft,
                          blocks: draft.blocks.filter((b) => b.id !== block.id),
                        })
                      }
                    >
                      Убрать блок
                    </button>
                  </div>
                  <label>
                    Подпись редактора
                    <input
                      value={block.caption}
                      disabled={pinned}
                      maxLength={1000}
                      onChange={(e) =>
                        changeBlock(block.id, { caption: e.target.value })
                      }
                    />
                  </label>
                  <div className="page-toolbar">
                    <label>
                      Ширина
                      <select
                        value={block.width}
                        disabled={pinned}
                        onChange={(e) =>
                          changeBlock(block.id, {
                            width: e.target.value as "full" | "half",
                          })
                        }
                      >
                        <option value="full">Вся строка</option>
                        <option value="half">Половина строки</option>
                      </select>
                    </label>
                    <label>
                      Фильтр таблицы/графика
                      <input
                        value={block.filter_text}
                        maxLength={200}
                        disabled={pinned}
                        onChange={(e) =>
                          changeBlock(block.id, { filter_text: e.target.value })
                        }
                      />
                    </label>
                    <label className="page-checkbox">
                      <input
                        type="checkbox"
                        checked={block.required}
                        disabled={pinned}
                        onChange={(e) =>
                          changeBlock(block.id, { required: e.target.checked })
                        }
                      />
                      Необходим для выводов
                    </label>
                  </div>
                </li>
              );
            })}
          </ol>
          <div className="page-toolbar">
            <button
              className="button primary"
              disabled={
                busy ||
                !draft.title.trim() ||
                (!record && createKind === "team" && !teamId)
              }
              onClick={() => void save()}
            >
              Сохранить черновик
            </button>
            <button
              className="button ghost"
              onClick={() => {
                setEditing(false);
                setDirty(false);
                if (record) setDraft(record.draft.page);
              }}
            >
              Отменить локальные правки
            </button>
            <span className="muted">
              Предпросмотр показывает компоновку будущей публикации.
            </span>
          </div>
        </div>
      )}
      {record && !editing && (
        <div className="page-toolbar">
          <label>
            Версия
            <select
              aria-label="Версия исследовательской страницы"
              value={view}
              onChange={(e) => void chooseView(e.target.value)}
            >
              <option value="draft">
                Текущий черновик · {record.revision}
              </option>
              {record.publication && (
                <option value="published">
                  Текущая публикация · {record.publication.revision}
                </option>
              )}
              {record.history
                .filter((h) => h.id !== record.published_id)
                .map((h) => (
                  <option key={h.id} value={h.id}>
                    Архив · {date(h.created)}
                  </option>
                ))}
            </select>
          </label>
          <label>
            Итог
            <select
              value={outcome}
              onChange={(e) =>
                setOutcome(e.target.value as "complete" | "inconclusive")
              }
            >
              <option value="complete">Проверенные выводы</option>
              <option value="inconclusive">
                Выводы с установленными ограничениями
              </option>
            </select>
          </label>
          <button
            className="button secondary small"
            disabled={busy || !shown?.blocks.length}
            onClick={() =>
              void publish(release?.revision_id || record.draft.id)
            }
          >
            {view !== "draft" && view !== "published"
              ? "Восстановить как новую публикацию"
              : "Опубликовать выбранную версию"}
          </button>
          <a
            className="button ghost small"
            href={`/api/projects/${project.id}/pages/${record.id}/export${release ? `?release_id=${release.id}` : ""}`}
          >
            Экспорт JSON с доказательствами
          </a>
        </div>
      )}
      {shown && (
        <>
          <label className="page-view-filter">
            Временный фильтр представления
            <input
              aria-label="Временный фильтр страницы"
              value={filter}
              maxLength={200}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Фильтровать подготовленные таблицы и графики"
            />
            <small>
              Работает локально без модели. Временное значение заменяет
              сохранённый фильтр блока.
            </small>
          </label>
          <PagePreview
            projectId={project.id}
            page={shown}
            release={editing || view === "draft" ? null : release}
            filter={filter}
            refresh={String(refresh)}
          />
        </>
      )}
      {record && !editing && (
        <section className="page-user-feedback">
          <h3>Закрепления и текущие заметки пользователя</h3>
          <div className="page-toolbar">
            {record.draft.page.blocks.map((b, i) => (
              <button
                key={b.id}
                className="button secondary small"
                disabled={busy}
                onClick={() =>
                  void feedback({
                    action: record.user_state.pins[b.id] ? "unpin" : "pin",
                    block_id: b.id,
                  })
                }
              >
                {record.user_state.pins[b.id] ? "Открепить" : "Закрепить"} блок{" "}
                {i + 1}
              </button>
            ))}
          </div>
          {Object.values(record.user_state.comments).map((c) => (
            <article key={c.id}>
              <p>{c.text}</p>
              <button
                disabled={busy}
                onClick={() =>
                  void feedback({ action: "delete_comment", comment_id: c.id })
                }
              >
                Убрать из текущих заметок
              </button>
            </article>
          ))}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!comment.trim()) return;
              void act(async () => {
                await api.pageFeedback(
                  project.id,
                  record.id,
                  record.feedback_revision,
                  {
                    action: "comment",
                    comment_id: commentId.current,
                    text: comment,
                  },
                );
                setComment("");
                commentId.current = crypto.randomUUID();
              });
            }}
          >
            <label>
              Заметка
              <textarea
                value={comment}
                maxLength={4000}
                onChange={(e) => setComment(e.target.value)}
                aria-label="Заметка к исследовательской странице"
              />
            </label>
            <button
              className="button secondary small"
              disabled={busy || !comment.trim()}
            >
              Сохранить заметку
            </button>
          </form>
        </section>
      )}
      <details className="page-comparison">
        <summary>Сравнить условия исследовательских итераций</summary>
        <div className="page-toolbar">
          <label>
            Первый запуск
            <select
              aria-label="Первый запуск для сравнения"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
            >
              <option value="">Выберите запуск</option>
              {catalog.runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {date(r.created)} · {r.question.slice(0, 70)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Второй запуск
            <select
              aria-label="Второй запуск для сравнения"
              value={compareRun}
              onChange={(e) => setCompareRun(e.target.value)}
            >
              <option value="">Выберите запуск</option>
              {catalog.runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {date(r.created)} · {r.question.slice(0, 70)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {comparison && sourceRun && (
          <>
            <p>
              {sameConditions
                ? "Снимки данных, определений и выбранных документных источников совпадают. Сопоставимость выводов всё равно требует проверки."
                : "Условия различаются. Прямое сравнение чисел требует проверки определений и применимости источников."}
            </p>
            <div className="page-compare-grid">
              {[sourceRun, comparison].map((r, i) => (
                <article key={i}>
                  <h4>{date(r.created)}</h4>
                  <p>{r.question}</p>
                  {Object.entries(r.definitions).map(([key, value]) => (
                    <p key={key}>
                      <strong>{key}:</strong> {value}
                    </p>
                  ))}
                  <p>Документных источников: {r.document_sources.length}</p>
                  <details>
                    <summary>Версии данных</summary>
                    {r.inputs.map((f) => (
                      <p key={f.name}>
                        {f.name} · SHA256 {f.sha256.slice(0, 12)}
                      </p>
                    ))}
                  </details>
                </article>
              ))}
            </div>
          </>
        )}
      </details>
    </section>
  );
}
