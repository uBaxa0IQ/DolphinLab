import {
  ArrowUp,
  ArrowUpRight,
  CheckCheck,
  ChevronDown,
  Command,
  Loader2,
  MoreHorizontal,
  Paperclip,
  Plus,
  Sparkles,
  X,
} from "lucide-react";
import type { FormEvent, Ref } from "react";
import type { ChatIntent, ChatMessage, PMEvidence, Project } from "../../types";

type Props = {
  project: Project;
  executionEnabled: boolean;
  mobileOpen: boolean;
  loading: boolean;
  sending: boolean;
  messages: ChatMessage[];
  draft: string;
  directive: boolean;
  intent: ChatIntent;
  onIntent: (value: ChatIntent) => void;
  onHistory: () => void;
  error: string;
  chatEnd: Ref<HTMLDivElement>;
  onClose: () => void;
  onNotice: (message: string) => void;
  onDraft: (value: string) => void;
  onDirective: (value: boolean) => void;
  onSend: (event?: FormEvent, suggested?: string) => Promise<void>;
  onRetry: (messageId: string) => void;
  onFiles: () => void;
};

export default function ChatPanel({
  project,
  executionEnabled,
  mobileOpen,
  loading,
  sending,
  messages,
  draft,
  directive,
  intent,
  onIntent,
  onHistory,
  error,
  chatEnd,
  onClose,
  onNotice,
  onDraft,
  onDirective,
  onSend,
  onRetry,
  onFiles,
}: Props) {
  return (
    <aside
      className={`chat-panel ${mobileOpen ? "mobile-open" : ""}`}
      aria-label="Чат с project manager"
    >
      <header className="chat-header">
        <span className="pm-avatar">
          <Sparkles size={19} strokeWidth={1.5} />
          <i />
        </span>
        <div>
          <h2>Project manager</h2>
          <span>{project.demo ? "На связи" : "История проекта"}</span>
        </div>
        <button
          className="icon-button"
          title="О project manager"
          aria-label="О project manager"
          onClick={() =>
            onNotice(
              project.demo
                ? "Демочат: ответы подготовлены для просмотра дизайна, модель не вызывается."
                : "PM отвечает на вопросы по данным, сохраняет договорённости и помогает подготовить продолжение. Вопрос не запускает исследование. Общение использует отдельный бюджет проекта.",
            )
          }
        >
          <MoreHorizontal size={20} />
        </button>
        <button
          className="icon-button mobile-chat-button"
          onClick={() => onClose()}
          aria-label="Скрыть чат"
        >
          <X size={18} />
        </button>
      </header>
      <div className="chat-scope">
        <span className="tiny-dot" />
        {project.name}
        <ChevronDown size={12} />
      </div>
      <div className="chat-messages">
        <div className="chat-day">
          {project.demo ? "Сегодня · демонстрация" : "История переписки"}
        </div>
        {loading && (
          <p className="chat-loading">
            <Loader2 size={16} className="spin" />
            Загружаем историю…
          </p>
        )}
        {!loading && messages.length === 0 && (
          <div className="chat-welcome">
            <Sparkles size={26} strokeWidth={1.3} />
            <h3>Начнём с задачи</h3>
            <p>
              Расскажи, что хочешь исследовать. Переписка останется в проекте.
            </p>
          </div>
        )}
        {messages.map((message) => (
          <div className={`chat-message ${message.role}`} key={message.id}>
            {message.role === "assistant" && (
              <div className="message-author">
                <span className="mini-pm">
                  <Sparkles size={12} />
                </span>
                Project manager<span>{message.time}</span>
              </div>
            )}
            {message.directive && (
              <span className="directive-label">
                <ArrowUpRight size={11} />
                Поручение техлиду
              </span>
            )}
            <div className="message-body">{message.content}</div>
            {message.role === "user" &&
              message.requested_intent === "auto" &&
              message.intent &&
              message.intent !== "auto" && (
                <div className="pending-message" title={message.intent_reason}>
                  PM определил:{" "}
                  {
                    {
                      question: "вопрос по данным",
                      instruction: "поручение",
                      followup: "следующая итерация",
                      clarification: "нужно уточнение",
                    }[message.intent]
                  }
                </div>
              )}
            {message.role === "user" && (
              <div className="message-time">
                {message.time}
                <CheckCheck size={12} />
              </div>
            )}
            {message.status === "pending" && (
              <div className="pending-message">
                {executionEnabled
                  ? "Сохранено · ожидает ответа PM"
                  : "Сохранено · вызовы модели отключены"}
              </div>
            )}
            {message.status === "processing" && (
              <div className="pending-message">PM готовит ответ…</div>
            )}
            {message.status === "failed" && (
              <div className="failed-message">
                <span>{message.error || "Не удалось получить ответ PM"}</span>
                <button onClick={() => onRetry(message.id)}>Повторить</button>
              </div>
            )}
            {message.directive_status && (
              <div className="pending-message">
                Поручение: {message.directive_status}
                {message.directive_decision
                  ? ` · ${message.directive_decision}`
                  : ""}
              </div>
            )}
            {(message.role === "assistant" || message.status === "failed") &&
              message.evidence?.map((evidence) => (
                <QuestionEvidence
                  key={evidence.id}
                  evidence={evidence}
                  onHistory={onHistory}
                />
              ))}
          </div>
        ))}
        {sending && (
          <div className="typing">
            <span />
            <span />
            <span />
          </div>
        )}
        <div ref={chatEnd} />
      </div>
      <div className="chat-compose-area">
        {!project.demo && executionEnabled && (
          <p className="conversation-cost">
            {!project.conversation_budget
              ? "Бюджет PM не задан — ответы модели выключены. Настрой его в параметрах проекта."
              : `Учтено за общение: ${(project.conversation_usage?.accounted_cost || 0).toLocaleString("ru-RU", { maximumFractionDigits: 4 })} из ${project.conversation_budget} ${project.conversation_usage?.currency || "в валюте тарифа"}`}
          </p>
        )}
        {!project.demo && (
          <label className="chat-intent-label">
            Режим сообщения
            <select
              value={intent}
              onChange={(event) => onIntent(event.target.value as ChatIntent)}
              aria-label="Режим сообщения"
              disabled={sending}
            >
              <option value="auto">Автоматически</option>
              <option value="question">Вопрос по данным</option>
              <option value="instruction">Поручение текущему запуску</option>
              <option value="followup">Следующая итерация</option>
            </select>
            <small>
              {intent === "auto"
                ? "PM определит намерение по диалогу и уточнит, если нужно. Исследование запускается отдельно."
                : intent === "question"
                  ? "Ответ или небольшой расчёт без запуска исследования."
                  : intent === "followup"
                    ? "PM уточнит цель и подготовит итерацию. Запуск — отдельно."
                    : "Поручение адресуется текущему запуску и не переносится в следующий."}
            </small>
          </label>
        )}
        {project.demo && (
          <div className="suggestions">
            <button
              onClick={() => void onSend(undefined, "Какой сейчас прогресс?")}
              disabled={sending}
            >
              Какой прогресс?
              <ArrowUpRight size={12} />
            </button>
            <button
              onClick={() => {
                onDirective(true);
                document.getElementById("chat-input")?.focus();
              }}
            >
              Дать поручение
              <Plus size={12} />
            </button>
          </div>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <form
          className={`chat-compose ${directive ? "directive" : ""}`}
          onSubmit={onSend}
        >
          {directive && (
            <div className="compose-mode">
              <ArrowUpRight size={12} />
              Поручение техлиду
              <button
                type="button"
                onClick={() => onDirective(false)}
                aria-label="Обычное сообщение"
              >
                <X size={12} />
              </button>
            </div>
          )}
          <textarea
            id="chat-input"
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            placeholder={
              directive
                ? "Что нужно изменить в работе?"
                : intent === "followup"
                  ? "Что проверить в следующей итерации?"
                  : intent === "auto"
                    ? "Задай вопрос или расскажи, что хочешь проверить…"
                    : "Задай вопрос по данным…"
            }
            rows={2}
            maxLength={20000}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void onSend();
              }
            }}
          />
          <div className="compose-tools">
            <button
              type="button"
              className="icon-button"
              onClick={() => {
                onFiles();
                onClose();
              }}
              aria-label="Файлы проекта"
              title="Файлы проекта"
            >
              <Paperclip size={16} />
            </button>
            <button
              type="button"
              className={`compose-directive ${directive ? "active" : ""}`}
              onClick={() => onDirective(!directive)}
              title="Передать поручение техлиду"
            >
              <Command size={13} />
              Поручение
            </button>
            <button
              className="onSend-button"
              type="submit"
              aria-label="Отправить сообщение"
              disabled={!draft.trim() || sending || loading}
            >
              {sending ? (
                <Loader2 size={16} className="spin" />
              ) : (
                <ArrowUp size={18} />
              )}
            </button>
          </div>
        </form>
        <div className="chat-footnote">
          {project.demo
            ? "Деморежим · без вызовов модели"
            : executionEnabled
              ? "Вопросы и расчёты · отдельный бюджет PM"
              : "Сообщения сохраняются · вызовы модели отключены"}
        </div>
      </div>
    </aside>
  );
}

function QuestionEvidence({
  evidence,
  onHistory,
}: {
  evidence: PMEvidence;
  onHistory: () => void;
}) {
  const result = evidence.result;
  const operators: Record<string, string> = {
    eq: "=",
    ne: "≠",
    gt: ">",
    gte: "≥",
    lt: "<",
    lte: "≤",
    is_null: "пусто",
    not_null: "не пусто",
  };
  if (evidence.kind === "prepare_followup" && !result.error)
    return (
      <div className="question-evidence">
        <p>
          Подготовлена итерация {result.sequence}. Вычисления ещё не запущены.
        </p>
        <button className="button secondary small" onClick={onHistory}>
          Открыть историю
        </button>
      </div>
    );
  const metricNames: Record<string, string> = {
    count: "Количество",
    sum: "Сумма",
    mean: "Среднее",
    min: "Минимум",
    max: "Максимум",
  };
  return (
    <details className="question-evidence">
      <summary>
        {result.error
          ? "Расчёт не выполнен"
          : `Расчёт · ${result.name || "данные"}`}
      </summary>
      {result.error ? (
        <p>{result.error}</p>
      ) : (
        <>
          <p>
            {metricNames[result.metric || ""] ||
              (result.operation === "schema"
                ? "Структура таблицы"
                : "Просмотр данных")}
            {result.column ? `: ${result.column}` : ""}
          </p>
          {result.groups?.map((group, index) => (
            <p key={index}>
              <strong>
                {Object.entries(group.group)
                  .map(([key, value]) => `${key}: ${value}`)
                  .join(", ") || "Всего"}
              </strong>
              : {group.value ?? "нет значений"}
            </p>
          ))}
          {result.operation === "schema" && <p>{result.columns?.join(", ")}</p>}
          {result.rows?.map((row, index) => (
            <p key={index}>
              {Object.entries(row)
                .map(([key, value]) => `${key}: ${value}`)
                .join(" · ")}
            </p>
          ))}
          {result.operation === "preview" && (
            <p>
              Предпросмотр первых {result.rows?.length || 0} строк; значения
              ячеек показаны до 200 символов.
            </p>
          )}
          <p>
            Прочитано строк: {result.rows_scanned}. После фильтров:{" "}
            {result.matched_rows}.
          </p>
          {result.filters?.map((filter, index) => (
            <p key={index}>
              {filter.column} {operators[filter.operator] || filter.operator}{" "}
              {filter.value || ""}
            </p>
          ))}
          {!!result.excluded_empty_values && (
            <p>Пропущено пустых значений: {result.excluded_empty_values}.</p>
          )}
          {result.truncated && (
            <p>Показаны первые 50 из {result.total_groups} групп.</p>
          )}
          <small>
            Источник: {result.input_id?.slice(0, 8)} · расчёт{" "}
            {evidence.id.slice(0, 8)}. Результат вычисления, без независимой
            проверки.
          </small>
        </>
      )}
    </details>
  );
}
