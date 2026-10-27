import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { MemoryRecord } from "../../types";

const kinds: Record<string, string> = {
  user_statement: "Со слов пользователя",
  observation: "Наблюдение",
  hypothesis: "Гипотеза",
  open_question: "Открытый вопрос",
};
export default function ProjectMemory({
  projectId,
  eventId,
}: {
  projectId: string;
  eventId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<MemoryRecord[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    api
      .memory(projectId, offset)
      .then((data) => {
        if (active) {
          setRecords(data);
          setError("");
        }
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, projectId, eventId, offset]);
  return (
    <details
      className="project-memory"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Память проекта</summary>
      {loading && <p>Загружаем записи…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && !records.length && (
        <p>
          Здесь появятся сохранённые договорённости, наблюдения и открытые
          вопросы.
        </p>
      )}
      {records.map((record) => (
        <article
          key={record.id}
          className={record.current ? "" : "memory-historical"}
        >
          <strong>
            {kinds[record.kind] || record.kind}
            {!record.current ? " · требует проверки применимости" : ""}
            {record.superseded ? " · заменена" : ""}
          </strong>
          <p>{record.text}</p>
          {record.source_quote && (
            <blockquote>{record.source_quote}</blockquote>
          )}
          <small>
            Источник: сообщение {record.message_id.slice(0, 8)}
            {record.query_id ? ` · расчёт ${record.query_id.slice(0, 8)}` : ""}.
          </small>
        </article>
      ))}
      {(offset > 0 || records.length === 100) && (
        <div className="research-actions">
          <button
            className="button secondary small"
            disabled={loading || offset === 0}
            onClick={() => setOffset((value) => Math.max(0, value - 100))}
          >
            Новые записи
          </button>
          <button
            className="button secondary small"
            disabled={loading || records.length < 100}
            onClick={() => setOffset((value) => value + 100)}
          >
            Предыдущие записи
          </button>
        </div>
      )}
    </details>
  );
}
