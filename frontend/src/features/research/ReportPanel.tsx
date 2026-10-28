import Markdown from "../../components/Markdown";
import { shortText } from "../../lib/presentation";
import type { PageBlock } from "../../types/pages";
import { Component, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import type { Project, ResearchRun } from "../../types";
import type {
  ChartData,
  ReportData,
  ResultIndex,
  ResultRecord,
  TableData,
} from "../../types/reports";
import AnalyticalChart from "./AnalyticalChart";

const statusNames: Record<string, string> = {
  verified: "Проверено",
  reported: "Не проверено",
  refuted: "Опровергнуто",
  inconclusive: "Недостаточно оснований",
  broken: "Доказательство недоступно",
};
export class BlockBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <p role="alert" className="form-error">
        Этот блок не удалось отобразить. Остальные результаты доступны.
      </p>
    ) : (
      this.props.children
    );
  }
}

export default function ReportPanel({ project }: { project: Project }) {
  const [runs, setRuns] = useState<ResearchRun[]>([]),
    [runId, setRunId] = useState(project.latest_run?.id || "");
  const [index, setIndex] = useState<ResultIndex | null>(null),
    [pageId, setPageId] = useState(""),
    [error, setError] = useState("");
  const manualPageSelection = useRef(false);
  useEffect(() => {
    let active = true;
    api
      .runs(project.id)
      .then((items) => {
        if (active) {
          setRuns(items);
          setRunId((id) => id || items[0]?.id || "");
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [project.id, project.latest_run?.updated]);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    setError("");

    api
      .results(project.id, runId)
      .then((data) => {
        if (active) {
          setIndex(data);
          setPageId((id) =>
            manualPageSelection.current && data.pages.some((p) => p.id === id)
              ? id
              : data.current?.page_id || data.pages.at(-1)?.id || "",
          );
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [project.id, runId, project.latest_run?.updated]);
  if (!runId) return null;
  const page = index?.pages.find((p) => p.id === pageId);
  return (
    <section className="report-panel" aria-label="Аналитический отчёт">
      <div className="report-toolbar">
        <div>
          <span className="eyebrow">РЕЗУЛЬТАТЫ ИССЛЕДОВАНИЯ</span>
          <h2>{page?.title || "Аналитические результаты"}</h2>
        </div>
        <label>
          Запуск
          <select
            aria-label="Запуск отчёта"
            value={runId}
            onChange={(e) => {
              manualPageSelection.current = false;
              setIndex(null);
              setPageId("");
              setRunId(e.target.value);
            }}
          >
            {runs.map((r) => (
              <option value={r.id} key={r.id}>
                {new Date(r.created).toLocaleDateString("ru-RU")} ·{" "}
                {new Date(r.created).toLocaleTimeString("ru-RU", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {!error && !index && <p className="muted">Загружаем результаты…</p>}
      {index && (
        <>
          {!!index.pages.length && (
            <div className="report-versions">
              <label>
                Версия страницы
                <select
                  aria-label="Версия страницы отчёта"
                  value={pageId}
                  onChange={(e) => {
                    manualPageSelection.current = true;
                    setPageId(e.target.value);
                  }}
                >
                  {index.pages.map((p, i) => (
                    <option key={p.id} value={p.id}>
                      Версия {i + 1} · {p.draft ? "черновик" : "публикация"} ·{" "}
                      {new Date(p.created * 1000).toLocaleString("ru-RU")}
                    </option>
                  ))}
                </select>
              </label>
              {page && (
                <span>
                  {page.draft
                    ? "Черновик"
                    : index.current?.page_id === page.id
                      ? "Опубликовано"
                      : "Архив публикации"}
                  {page.outcome === "inconclusive"
                    ? " · Вывод ограничен данными"
                    : ""}
                </span>
              )}
            </div>
          )}
          {page ? (
            <BlockBoundary key={page.id}>
              <ResultBlock
                projectId={project.id}
                runId={runId}
                id={page.report_id}
                refresh={project.latest_run?.updated}
                root
              />
            </BlockBoundary>
          ) : (
            <>
              <p className="muted">
                {index.results.length
                  ? "Результаты сохранены. Страница отчёта ещё не собрана."
                  : "Здесь появятся таблицы, графики и выводы со ссылками на выполненные расчёты."}
              </p>
              {index.results
                .filter((r) => !r.legacy)
                .slice(-12)
                .map((r) => (
                  <details key={r.id}>
                    <summary>
                      {r.title} · версия {r.revision}
                    </summary>
                    <BlockBoundary>
                      <ResultBlock
                        projectId={project.id}
                        runId={runId}
                        id={r.id}
                      />
                    </BlockBoundary>
                  </details>
                ))}
            </>
          )}
          {!!index.results.filter((r) => r.legacy).length && (
            <details>
              <summary>ML-кандидаты прежнего реестра</summary>
              {index.results
                .filter((r) => r.legacy)
                .map((r) => (
                  <p key={r.id}>
                    {r.id} · {r.status} · источник {r.job_id}
                  </p>
                ))}
            </details>
          )}
        </>
      )}
    </section>
  );
}

export function ResultBlock({
  projectId,
  runId,
  id,
  root = false,
  refresh = "",
  filterText = "",
  presentation,
}: {
  projectId: string;
  runId: string;
  id: string;
  root?: boolean;
  refresh?: string;
  filterText?: string;
  presentation?: PageBlock["presentation"];
}) {
  const [result, setResult] = useState<ResultRecord | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .result(projectId, runId, id)
      .then((r) => {
        if (active) setResult(r);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [projectId, runId, id, refresh]);
  if (error)
    return (
      <p className="form-error" role="alert">
        Блок недоступен: {error}
      </p>
    );
  if (!result) return <p className="muted">Загружаем блок…</p>;
  const p = result.payload;
  const reportSummary =
    result.kind === "report" ? (
      <>
        <Markdown
          className="report-prose report-summary"
          text={(p as ReportData).summary}
          technical
        />
        <Limitations items={(p as ReportData).limitations} />
      </>
    ) : null;
  return (
    <article className={`report-block ${root ? "report-root" : ""}`}>
      {!root && (
        <h3>{shortText(presentation?.heading || result.title, 220)}</h3>
      )}
      <span className={`report-status ${result.status}`}>
        {statusNames[result.status] || result.status}
      </span>
      {!result.applicable && (
        <p className="form-error">
          Используются сохранённые версии данных или методики. Учитывайте их при
          сравнении с новыми результатами.
        </p>
      )}
      {result.status === "broken" ? (
        <p className="form-error">
          Подтверждающие материалы недоступны. Результат сохранён, но его нужно
          проверить заново.
        </p>
      ) : presentation?.view === "chart" &&
        presentation.chart &&
        (result.kind === "table" || result.kind === "dataset") ? (
        <>
          <AnalyticalChart
            spec={{ ...presentation.chart, table_id: id, caption: "" }}
            table={p as TableData}
          />
          <details className="selection-details">
            <summary>Данные графика</summary>
            <PreparedTable data={p as TableData} filterText={filterText} />
          </details>
        </>
      ) : result.kind === "table" || result.kind === "dataset" ? (
        <PreparedTable data={p as TableData} filterText={filterText} />
      ) : result.kind === "metric" ? (
        <div className="report-metric">
          <strong>
            {(p as { value: number }).value.toLocaleString("ru-RU", {
              maximumSignificantDigits: 8,
            })}
          </strong>
          <span>{(p as { unit: string }).unit}</span>
          <details className="selection-details">
            <summary>
              О выборке · {(p as { sample_size: number }).sample_size}{" "}
              наблюдений
            </summary>
            <Markdown text={(p as { selection: string }).selection} technical />
          </details>
        </div>
      ) : result.kind === "finding" ? (
        <>
          <Markdown
            className="report-prose"
            text={(p as { text: string }).text}
            technical
          />
          <Limitations items={(p as { limitations: string[] }).limitations} />
        </>
      ) : result.kind === "chart" ? (
        <ChartBlock
          projectId={projectId}
          runId={runId}
          refresh={refresh}
          spec={p as ChartData}
          filterText={filterText}
        />
      ) : result.kind === "report" ? (
        <>
          {presentation?.view === "summary" ? (
            <details className="report-summary-details">
              <summary>Прочитать проверенный отчёт</summary>
              {reportSummary}
            </details>
          ) : (
            reportSummary
          )}
          {presentation?.view !== "summary" && (
            <div className="report-blocks">
              {(p as ReportData).blocks.map((block, i) => (
                <section key={id + ":" + i}>
                  <BlockBoundary>
                    <ResultBlock
                      projectId={projectId}
                      runId={runId}
                      refresh={refresh}
                      id={block.result_id}
                      filterText={filterText}
                    />
                  </BlockBoundary>
                  {block.caption && <p className="muted">{block.caption}</p>}
                  {!block.required && <small>Дополнительный блок</small>}
                </section>
              ))}
            </div>
          )}
        </>
      ) : null}
      <details className="report-evidence">
        <summary>Источники и проверка</summary>
        <p>
          Результат {result.id} · автор {result.author} · расчёт{" "}
          {result.job_id || "компоновка по сохранённым результатам"}
        </p>
        {result.input_versions.map((f) => (
          <p key={f.id}>
            {f.name} · версия {f.id.slice(0, 8)} · SHA256{" "}
            {f.sha256.slice(0, 12)}
          </p>
        ))}
        <a
          href={`/api/projects/${projectId}/runs/${runId}/evidence/${result.artifact_id}`}
        >
          Скачать исходник блока (JSON)
        </a>
        {result.evidence_files?.map((file) => (
          <a
            key={file.path}
            href={`/api/projects/${projectId}/runs/${runId}/artifact?path=${encodeURIComponent(file.path)}`}
          >
            {file.label}
          </a>
        ))}
        {result.assessment && (
          <>
            <p>
              Проверяющий: {result.assessment.reviewer} · job{" "}
              {result.assessment.job_id}
            </p>
            <p>{result.assessment.conclusion}</p>
            {result.assessment.checks.map((c, i) => (
              <p key={i}>
                {c.passed ? "✓" : "×"} {c.name}: {c.detail}
              </p>
            ))}
            <a
              href={`/api/projects/${projectId}/runs/${runId}/evidence/${result.assessment.artifact_id}`}
            >
              Скачать доказательство проверки
            </a>
          </>
        )}
      </details>
    </article>
  );
}

function Limitations({ items }: { items: string[] }) {
  return items.length ? (
    <aside className="report-limitations">
      <strong>Ограничения вывода</strong>
      {items.map((s, i) => (
        <Markdown key={i} text={s} technical />
      ))}
    </aside>
  ) : null;
}
export function PreparedTable({
  data,
  filterText = "",
}: {
  data: TableData;
  filterText?: string;
}) {
  const columns = data.columns
    .map((column, index) => ({ column, index }))
    .filter(
      ({ column }) =>
        !/(?:sha256|(?:version|chunk|document|artifact|run|result|assessment)_id)/i.test(
          column.name + " " + column.label,
        ),
    );
  const rows = data.rows.filter(
    (row) => !/^\s*SHA256\b/i.test(String(row[0] || "")),
  );
  const technical =
    columns.length !== data.columns.length || rows.length !== data.rows.length;
  if (!technical) return <RawTable data={data} filterText={filterText} />;
  return (
    <>
      {columns.length > 0 && (
        <RawTable
          data={{
            ...data,
            columns: columns.map((c) => c.column),
            rows: rows.map((row) => columns.map((c) => row[c.index])),
          }}
          filterText={filterText}
        />
      )}
      <details className="technical-details">
        <summary>Технические атрибуты источников</summary>
        <RawTable data={data} filterText={filterText} />
      </details>
    </>
  );
}

function RawTable({
  data,
  filterText = "",
}: {
  data: TableData;
  filterText?: string;
}) {
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [filterText]);
  const rows = filterText
    ? data.rows.filter((row) =>
        row.some((value) =>
          String(value ?? "")
            .toLocaleLowerCase()
            .includes(filterText.toLocaleLowerCase()),
        ),
      )
    : data.rows;
  return (
    <>
      <details className="selection-details">
        <summary>
          О выборке · {data.sample_size.toLocaleString("ru-RU")} наблюдений
        </summary>
        <Markdown text={data.selection} technical />
        <p>Строк в подготовленной таблице: {data.rows.length}</p>
      </details>
      {filterText && (
        <p className="muted">
          Фильтр представления: {rows.length} строк. Исходные данные не
          изменены.
        </p>
      )}
      <div className="report-table-scroll">
        <table>
          <thead>
            <tr>
              {data.columns.map((c) => (
                <th key={c.name}>
                  {c.label}
                  {c.unit &&
                  !c.label
                    .toLocaleLowerCase()
                    .includes(c.unit.toLocaleLowerCase())
                    ? ` (${c.unit})`
                    : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(offset, offset + 30).map((r, i) => (
              <tr key={i}>
                {r.map((v, j) => (
                  <td key={j}>
                    {v === null
                      ? "—"
                      : typeof v === "number"
                        ? v.toLocaleString("ru-RU", {
                            maximumSignificantDigits: 8,
                          })
                        : v}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 30 && (
        <div className="report-table-pages">
          <button
            disabled={!offset}
            onClick={() => setOffset((n) => Math.max(0, n - 30))}
          >
            Назад
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 30, rows.length)}
          </span>
          <button
            disabled={offset + 30 >= rows.length}
            onClick={() => setOffset((n) => n + 30)}
          >
            Далее
          </button>
        </div>
      )}
    </>
  );
}
function ChartBlock({
  projectId,
  runId,
  spec,
  refresh,
  filterText = "",
}: {
  projectId: string;
  runId: string;
  spec: ChartData;
  refresh?: string;
  filterText?: string;
}) {
  const [table, setTable] = useState<ResultRecord | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .result(projectId, runId, spec.table_id)
      .then((r) => {
        if (active) setTable(r);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [projectId, runId, spec.table_id, refresh]);
  if (error)
    return <p className="form-error">Источник графика недоступен: {error}</p>;
  if (!table) return <p>Загружаем данные графика…</p>;
  if (table.status === "broken")
    return (
      <p className="form-error">
        Таблица графика не прошла проверку целостности.
      </p>
    );
  const data = table.payload as TableData;
  const view = filterText
    ? {
        ...data,
        rows: data.rows.filter((row) =>
          row.some((value) =>
            String(value ?? "")
              .toLocaleLowerCase()
              .includes(filterText.toLocaleLowerCase()),
          ),
        ),
      }
    : data;
  return (
    <>
      {filterText && (
        <p className="muted">
          Локальный фильтр представления: {view.rows.length} строк. Исходные
          расчёты не изменены.
        </p>
      )}
      <AnalyticalChart spec={spec} table={view} />
      <details>
        <summary>
          Данные графика · {statusNames[table.status] || table.status}
        </summary>
        <PreparedTable data={data} filterText={filterText} />
      </details>
    </>
  );
}
