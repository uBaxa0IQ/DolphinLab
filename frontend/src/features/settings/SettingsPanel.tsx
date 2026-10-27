import {
  Check,
  KeyRound,
  Loader2,
  Plug,
  Trash2,
  UserRound,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../lib/api";
import type { WorkspaceSettings } from "../../types";

export default function SettingsPanel({
  saved,
  close,
}: {
  saved: (settings: WorkspaceSettings) => void;
  close: () => void;
}) {
  const [tab, setTab] = useState<"profile" | "api">("profile");
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [configured, setConfigured] = useState(false);
  const [clearKey, setClearKey] = useState(false);
  const [executionEnabled, setExecutionEnabled] = useState(false);
  const [pricingText, setPricingText] = useState("");

  useEffect(() => {
    let active = true;
    api
      .settings()
      .then((value) => {
        if (!active) return;
        setName(value.display_name);
        setBaseUrl(value.base_url);
        setModel(value.model);
        setConfigured(value.key_configured);
        setExecutionEnabled(value.execution_enabled);
        setPricingText(
          value.pricing && Object.keys(value.pricing).length
            ? JSON.stringify(value.pricing, null, 2)
            : "",
        );
        setLoaded(true);
      })
      .catch(() => {
        if (active)
          setError(
            "Не удалось загрузить настройки. Убедись, что локальный сервер запущен.",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!loaded || loading || busy || !name.trim()) return;
    setError("");
    setBusy(true);
    try {
      const pricing = pricingText.trim() ? JSON.parse(pricingText) : undefined;
      const result = await api.saveSettings({
        display_name: name.trim(),
        base_url: baseUrl.trim(),
        model: model.trim(),
        ...(key.trim() ? { api_key: key.trim() } : {}),
        clear_key: clearKey,
        pricing,
      });
      setKey("");
      saved(result);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-panel">
      <span className="eyebrow">ЛИЧНОЕ ПРОСТРАНСТВО</span>
      <h2>Настройки</h2>
      <div className="settings-tabs" role="tablist" aria-label="Настройки">
        <button
          role="tab"
          aria-selected={tab === "profile"}
          className={tab === "profile" ? "active" : ""}
          onClick={() => setTab("profile")}
        >
          <UserRound size={15} />
          Профиль
        </button>
        <button
          role="tab"
          aria-selected={tab === "api"}
          className={tab === "api" ? "active" : ""}
          onClick={() => setTab("api")}
        >
          <Plug size={15} />
          Подключение API
        </button>
      </div>
      {loading ? (
        <div className="settings-loading">
          <Loader2 size={20} className="spin" />
        </div>
      ) : (
        <form onSubmit={submit} autoComplete="off">
          {tab === "profile" ? (
            <div className="settings-profile">
              <span className="settings-avatar">
                {name.trim()[0]?.toUpperCase() || "Я"}
              </span>
              <label className="field-label">
                Имя
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={80}
                  required
                  placeholder="Как к тебе обращаться"
                />
              </label>
              <p className="field-help">
                Локальный профиль на этом компьютере.
              </p>
              <button
                type="button"
                className="settings-connection-link"
                onClick={() => setTab("api")}
              >
                <span className="connection-icon">
                  <KeyRound size={18} />
                </span>
                <span>
                  <strong>Подключение модели</strong>
                  <small>
                    {configured
                      ? "API-ключ сохранён"
                      : "Добавь API-ключ и адрес провайдера"}
                  </small>
                </span>
                {configured ? <Check size={15} /> : <Plug size={15} />}
              </button>
            </div>
          ) : (
            <div className="settings-api">
              <div className="api-provider-line">
                <span className="connection-icon">
                  <Plug size={18} />
                </span>
                <div>
                  <strong>OpenAI-compatible API</strong>
                  <p>Свой провайдер и модель для агентов.</p>
                </div>
              </div>
              <label className="field-label">
                Base URL
                <input
                  type="url"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  maxLength={1000}
                  placeholder="https://your-provider.com/v1"
                  spellCheck={false}
                />
              </label>
              <label className="field-label">
                Модель
                <input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  maxLength={200}
                  placeholder="Название модели у провайдера"
                  spellCheck={false}
                />
              </label>
              <div className="api-key-label">
                <span>API-ключ</span>
                {configured && !clearKey && (
                  <span className="key-status">
                    <Check size={11} />
                    Сохранён
                  </span>
                )}
              </div>
              <input
                className="api-key-input"
                type="password"
                value={key}
                onChange={(e) => {
                  setKey(e.target.value);
                  setClearKey(false);
                }}
                placeholder={
                  clearKey
                    ? "Ключ будет удалён при сохранении"
                    : configured
                      ? "Введи новый ключ для замены"
                      : "Вставь свой API-ключ"
                }
                autoComplete="new-password"
                maxLength={8192}
                spellCheck={false}
              />
              {configured && (
                <button
                  type="button"
                  className={`delete-key ${clearKey ? "marked" : ""}`}
                  onClick={() => {
                    setClearKey(!clearKey);
                    setKey("");
                  }}
                >
                  <Trash2 size={12} />
                  {clearKey ? "Отменить удаление" : "Удалить сохранённый ключ"}
                </button>
              )}
              <p className="field-help">
                Ключ хранится зашифрованным на сервере. Сохранение не вызывает
                модель.
              </p>
              <details className="pricing-details">
                <summary>Тариф и резерв стоимости</summary>
                <p className="field-help">
                  Для денежного лимита нужны подтверждённые цены и максимальная
                  ёмкость запроса. Формат JSON: currency, input_per_million,
                  output_per_million, max_input_tokens, max_output_tokens. Цены
                  указаны за миллион токенов. Длина генерации этим не
                  ограничивается.
                </p>
                <textarea
                  className="pricing-input"
                  rows={6}
                  value={pricingText}
                  onChange={(event) => setPricingText(event.target.value)}
                  placeholder="Значения из документации твоего провайдера"
                  spellCheck={false}
                />
              </details>
              <div className="settings-execution-note">
                <span className="tiny-dot" />
                {executionEnabled
                  ? "Исполнение агентов разрешено"
                  : "Вызовы модели отключены в Docker"}
              </div>
            </div>
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
              type="submit"
              className="button primary"
              disabled={!loaded || busy || !name.trim()}
            >
              {busy ? (
                <Loader2 size={15} className="spin" />
              ) : (
                <Check size={15} />
              )}
              Сохранить
            </button>
          </div>
        </form>
      )}
      {loading && error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
