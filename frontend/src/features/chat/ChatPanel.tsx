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
import type { ChatMessage, Project } from "../../types";

type Props = {
  project: Project;
  executionEnabled: boolean;
  mobileOpen: boolean;
  loading: boolean;
  sending: boolean;
  messages: ChatMessage[];
  draft: string;
  directive: boolean;
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
                : "Сообщения сохраняются на сервере. Ответы PM будут доступны после подключения исполнения агентов.",
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
                : "Спроси или предложи идею…"
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
              ? "PM общается с техлидом · история сохраняется"
              : "Сообщения сохраняются · вызовы модели отключены"}
        </div>
      </div>
    </aside>
  );
}
