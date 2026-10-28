import { useEffect, useRef, useState } from "react";
import {
  Download,
  FileSpreadsheet,
  FileText,
  Plus,
  Search,
  Upload,
  X,
} from "lucide-react";
import { api } from "../../lib/api";
import { size } from "../../lib/format";
import { isDocument } from "../../lib/materials";
import Markdown from "../../components/Markdown";
import type { Project } from "../../types";
import type { DocumentVersion, KnowledgeSource } from "../../types/documents";

const states: Record<string, string> = {
  queued: "Подготовка",
  processing: "Обрабатывается",
  ready: "Готов к работе",
  failed: "Нужна повторная обработка",
  deleted: "Удалён",
};

export default function Materials({
  project,
  changed,
}: {
  project: Project;
  changed: () => void;
}) {
  const [searched, setSearched] = useState(false);
  const [versions, setVersions] = useState<DocumentVersion[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [replacement, setReplacement] = useState<DocumentVersion | null>(null),
    [query, setQuery] = useState(""),
    [hits, setHits] = useState<KnowledgeSource[]>([]),
    [source, setSource] = useState<KnowledgeSource | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const refresh = () => api.documents(project.id).then(setVersions);
  useEffect(() => {
    if (project.demo) return;
    let active = true;
    const load = () =>
      api
        .documents(project.id)
        .then((v) => {
          if (active) {
            setVersions(v);
            const deleted=new Set(v.filter(item=>item.state==="deleted").map(item=>item.id));
            setSource(old=>old&&deleted.has(old.version_id||"")?null:old);
            setHits(old=>old.filter(hit=>!deleted.has(hit.version_id||"")));
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [project.id, project.demo]);
  async function action(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      try {
        if (!project.demo) await refresh();
      } catch {
        /* Keep the original action error. */
      }
      changed();
      setBusy(false);
    }
  }
  const current = versions.filter((v) => v.current && v.state !== "deleted");
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    await action(async () => {
      for (const file of Array.from(files)) {
        if (
          replacement ||
          isDocument(file.name)
        )
          await api.uploadDocument(project.id, file, replacement || undefined);
        else await api.upload(project.id, file);
      }
      setReplacement(null);
    });
  }
  return (
    <section className="materials" aria-label="Материалы проекта">
      <div className="section-heading">
        <div>
          <span className="eyebrow">ДАННЫЕ И КОНТЕКСТ</span>
          <h2>Материалы исследования</h2>
          <p className="muted">
            Таблицы для анализа, документы для понимания задачи.
          </p>
        </div>
        <button
          className="button primary"
          disabled={busy || project.demo}
          onClick={() => {
            setReplacement(null);
            setTimeout(() => picker.current?.click(), 0);
          }}
        >
          <Plus size={16} />
          Добавить материалы
        </button>
      </div>
      <input
        ref={picker}
        type="file"
        hidden
        multiple={!replacement}
        accept={replacement ? ".pdf,.docx,.md,.txt" : undefined}
        onChange={(e) => {
          void upload(e.target.files);
          e.target.value = "";
        }}
      />
      {busy && (
        <p role="status" className="muted">
          Сохраняем материалы…
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {!project.files.length && !current.length && (
        <button
          className="materials-drop"
          disabled={project.demo || busy}
          onClick={() => picker.current?.click()}
        >
          <Upload size={28} />
          <strong>Добавьте первые материалы</strong>
          <span>CSV, Excel, PDF, Word и другие файлы</span>
          <small>
            Или начните с вопроса в чате — помощник уточнит, какие данные нужны.
          </small>
        </button>
      )}
      {!!project.files.length && (
        <section className="material-group">
          <h3>
            Таблицы и исходные файлы <span>{project.files.length}</span>
          </h3>
          <div className="material-list">
            {project.files.map((f) => (
              <div className="material-row" key={f.id}>
                <span className="material-icon">
                  <FileSpreadsheet size={20} />
                </span>
                <div>
                  <strong>{f.name}</strong>
                  <small>{size(f.size)} · Данные исследования</small>
                </div>
                <a
                  className="icon-button"
                  aria-label={`Скачать ${f.name}`}
                  href={
                    project.demo
                      ? undefined
                      : `/api/projects/${project.id}/files/${f.id}`
                  }
                >
                  <Download size={17} />
                </a>
              </div>
            ))}
          </div>
        </section>
      )}
      {!!current.length && (
        <section className="material-group">
          <h3>
            Документы <span>{current.length}</span>
          </h3>
          <div className="material-list">
            {current.map((v) => (
              <article className="material-document" key={v.id}>
                <div className="material-row">
                  <span className="material-icon document">
                    <FileText size={20} />
                  </span>
                  <div>
                    <strong>{v.name}</strong>
                    <small>
                      Версия {v.revision} · {states[v.state]}
                    </small>
                  </div>
                  <a
                    className="icon-button"
                    aria-label={`Скачать ${v.name}`}
                    href={`/api/projects/${project.id}/documents/versions/${v.id}/original`}
                  >
                    <Download size={17} />
                  </a>
                  <details className="material-actions">
                    <summary aria-label={`Действия с ${v.name}`}>•••</summary>
                    <div>
                      <button
                        disabled={busy}
                        onClick={() => {
                          setReplacement(v);
                          setTimeout(() => picker.current?.click(), 0);
                        }}
                      >
                        Загрузить новую версию
                      </button>
                      {v.state === "failed" && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            void action(() =>
                              api.retryDocument(project.id, v.id),
                            )
                          }
                        >
                          Повторить обработку
                        </button>
                      )}
                      <button
                        className="danger-text"
                        disabled={busy}
                        onClick={() => {
                          if (
                            window.confirm(
                              `Удалить «${v.name}» и его версии? Источники в прежних исследованиях останутся в истории.`,
                            )
                          )
                            void action(() =>
                              api.removeDocument(project.id, v),
                            );
                        }}
                      >
                        Удалить документ
                      </button>
                    </div>
                  </details>
                </div>
                {(versions.some(
                  (old) => old.document_id === v.document_id && !old.current,
                ) ||
                  v.warnings.length > 0 ||
                  v.error) && (
                  <details className="material-history">
                    <summary>Версии и сведения об обработке</summary>
                    {versions
                      .filter(
                        (old) =>
                          old.document_id === v.document_id && !old.current,
                      )
                      .map((old) => (
                        <p key={old.id}>
                          <a
                            href={`/api/projects/${project.id}/documents/versions/${old.id}/original`}
                          >
                            Версия {old.revision} · {old.name}
                          </a>
                        </p>
                      ))}
                    {v.warnings.map((w, i) => (
                      <p key={i}>{w}</p>
                    ))}
                    {v.error && <p className="form-error">{v.error}</p>}
                  </details>
                )}
              </article>
            ))}
          </div>
        </section>
      )}
      {!!current.length && (
        <details className="workspace-disclosure document-search">
          <summary>
            <Search size={16} />
            Найти в документах
          </summary>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action(async () => {
                const r = await api.searchKnowledge(
                  project.id,
                  query,
                  "",
                  false,
                );
                setHits(r.results);
                setSearched(true);
                setSource(null);
              });
            }}
          >
            <input
              aria-label="Поиск по документам"
              placeholder="Например, как определяется выручка"
              maxLength={500}
              required
              value={query}
              onChange={(e) => {setQuery(e.target.value);setSearched(false)}}
            />
            <button className="button secondary" disabled={busy}>
              Найти
            </button>
          </form>
          {searched && !hits.length && !busy && <p className="muted">{current.some(v=>v.state==="ready") ? "Совпадений нет. Попробуйте другую формулировку." : "Пока нет обработанных документов. Проверьте их состояние выше."}</p>}
          {hits.map((h) => (
            <button
              className="source-hit"
              key={h.source_id}
              onClick={() =>
                void action(async () =>
                  setSource(await api.readSource(project.id, h.source_id)),
                )
              }
            >
              <strong>
                {current.find((v) => v.id === h.version_id)?.name ||
                  "Источник документа"}
              </strong>
              <p>{h.text?.slice(0, 280)}</p>
            </button>
          ))}
        </details>
      )}
      {source && (
        <article className="source-reader">
          <div className="section-heading">
            <h3>
              {current.find((v) => v.id === source.version_id)?.name ||
                "Фрагмент документа"}
            </h3>
            <button
              className="icon-button"
              aria-label="Закрыть источник"
              onClick={() => setSource(null)}
            >
              <X size={16} />
            </button>
          </div>
          {!current.some(v=>v.id===source.version_id) && <p className="muted">Прежняя версия документа. При продолжении исследования учитывайте изменение методики.</p>}
          <Markdown text={source.text || ""} />
          <details className="technical-details">
            <summary>Точная ссылка на источник</summary>
            <p>{source.citation}</p>
            <p>{source.source_id}</p>
          </details>
        </article>
      )}
    </section>
  );
}
