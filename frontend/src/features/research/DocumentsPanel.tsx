import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { DocumentVersion, KnowledgeSource } from "../../types/documents";
import "./DocumentsPanel.css";

const states = {
  queued: "В очереди",
  processing: "Обрабатывается",
  ready: "Доступен для поиска",
  failed: "Ошибка обработки",
  deleted: "Удалён",
};

function sourceLocation(source: KnowledgeSource) {
  const locator = source.locator || {};
  return [
    Array.isArray(locator.headings) ? locator.headings.join(" / ") : "",
    locator.page ? `Страница ${locator.page}` : "",
    locator.line_start
      ? `Строки ${locator.line_start}–${locator.line_end}`
      : "",
    typeof locator.block === "number" ? `Блок ${locator.block + 1}` : "",
    typeof locator.row === "number" ? `Строка таблицы ${locator.row}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export default function DocumentsPanel({ projectId }: { projectId: string }) {
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [replacement, setReplacement] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [historical, setHistorical] = useState(false);
  const [hits, setHits] = useState<KnowledgeSource[]>([]);
  const [source, setSource] = useState<KnowledgeSource | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true;
    const load = () =>
      api
        .documents(projectId)
        .then((v) => {
          if (active) {
            setVersions(v);
            const deleted = new Set(
              v.filter((x) => x.state === "deleted").map((x) => x.id),
            );
            setSource((current) =>
              current && deleted.has(current.version_id || "") ? null : current,
            );
            setHits((current) =>
              current.filter((x) => !deleted.has(x.version_id || "")),
            );
          }
        })
        .catch((e) => {
          if (active) setError(String(e.message));
        });
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [projectId]);
  async function action(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      setVersions(await api.documents(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="panel document-panel"
      style={{ padding: 20, marginTop: 20 }}
    >
      <h3>Документы и источники</h3>
      <p>
        Методики и отчёты: PDF с текстом, DOCX, Markdown/TXT, до 20 МиБ. Поиск
        охватывает извлечённый текст; предупреждения показывают ограничения
        обработки.
      </p>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 12,
          alignItems: "center",
        }}
      >
        <select
          aria-label="Загрузка или новая версия документа"
          value={replacement}
          onChange={(e) => setReplacement(e.target.value)}
        >
          <option value="">Новый документ</option>
          {versions
            .filter((v) => v.current)
            .map((v) => (
              <option key={v.id} value={v.id}>
                Новая версия: {v.name}
              </option>
            ))}
        </select>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => picker.current?.click()}
        >
          Добавить документ
        </button>
        <input
          ref={picker}
          hidden
          type="file"
          accept=".pdf,.docx,.md,.txt"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file)
              void action(async () => {
                await api.uploadDocument(
                  projectId,
                  file,
                  versions.find((v) => v.id === replacement),
                );
                setHits([]);
                setSource(null);
                setReplacement("");
                setFilter("");
              });
            e.target.value = "";
          }}
        />
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <ul>
        {versions.map((v) => (
          <li key={v.id} style={{ margin: "12px 0", overflowWrap: "anywhere" }}>
            <strong>
              {v.name} · v{v.revision}
            </strong>{" "}
            — {states[v.state]}
            {!v.current && v.state !== "deleted"
              ? " · Историческая версия"
              : ""}
            {v.usage.chunks !== undefined && (
              <span> · {v.usage.chunks} фрагм.</span>
            )}
            {v.state !== "deleted" && (
              <>
                {" "}
                ·{" "}
                <a
                  href={`/api/projects/${projectId}/documents/versions/${v.id}/original`}
                >
                  Оригинал
                </a>
              </>
            )}
            {v.state === "failed" && (
              <button
                disabled={busy}
                onClick={() =>
                  void action(() => api.retryDocument(projectId, v.id))
                }
              >
                Повторить обработку
              </button>
            )}
            {v.current && (
              <button
                disabled={busy}
                onClick={() => {
                  if (
                    window.confirm(
                      "Удалить документ, все его версии и живой индекс? Ранее сохранённые доказательства Run и ответы PM останутся в истории.",
                    )
                  )
                    void action(async () => {
                      const r = await api.removeDocument(projectId, v);
                      setNotice(r.notice);
                      setSource(null);
                      setHits([]);
                      setReplacement("");
                      setFilter("");
                    });
                }}
              >
                Удалить
              </button>
            )}
            {v.error && <p>{v.error}</p>}
            {v.warnings.map((w, i) => (
              <p key={i}>
                <small>{w}</small>
              </p>
            ))}
          </li>
        ))}
      </ul>
      {!versions.length && (
        <p>Документов пока нет. Датасеты загружаются отдельно выше.</p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void action(async () => {
            const r = await api.searchKnowledge(
              projectId,
              query,
              filter,
              historical,
            );
            setHits(r.results);
            setSource(null);
            setNotice(
              r.results.length
                ? ""
                : "Совпадений не найдено. Попробуйте другие термины; это не доказывает отсутствие информации в оригинале.",
            );
          });
        }}
        style={{ display: "flex", flexWrap: "wrap", gap: 12 }}
      >
        <input
          aria-label="Поиск по документам"
          placeholder="Определение выручки, возвраты…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          required
          maxLength={500}
        />
        <select
          aria-label="Версия для поиска"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="">Все документы</option>
          {versions
            .filter((v) => v.state === "ready")
            .map((v) => (
              <option key={v.id} value={v.id}>
                {v.name} · v{v.revision}
              </option>
            ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={historical}
            onChange={(e) => setHistorical(e.target.checked)}
          />{" "}
          Включая прежние версии
        </label>
        <button className="button secondary" disabled={busy}>
          Найти
        </button>
      </form>
      <ul>
        {hits.map((hit) => (
          <li key={hit.source_id}>
            <button
              onClick={() =>
                void action(async () => {
                  setSource(await api.readSource(projectId, hit.source_id));
                })
              }
            >
              {hit.citation}
            </button>
            <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {hit.text?.slice(0, 500)}
            </p>
          </li>
        ))}
      </ul>
      {source && (
        <article aria-label="Источник документа">
          <h4>
            {source.citation}
            {!versions.some((v) => v.id === source.version_id && v.current)
              ? " · Историческая версия"
              : ""}
          </h4>
          <p>{sourceLocation(source)}</p>
          {source.warnings?.map((w, i) => (
            <p key={i}>{w}</p>
          ))}
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {source.text}
          </pre>
          {source.neighbors?.map((n) => (
            <details key={n.source_id}>
              <summary>Соседний фрагмент · {n.citation}</summary>
              <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                {n.text}
              </pre>
            </details>
          ))}
        </article>
      )}
    </section>
  );
}
