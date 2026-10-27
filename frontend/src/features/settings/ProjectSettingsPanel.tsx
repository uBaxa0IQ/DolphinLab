import {
  Check,
  Compass,
  Cpu,
  Focus,
  Loader2,
  Scale,
  Sparkles,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../lib/api";
import type { Project, ProjectPolicy } from "../../types";

const defaults = (): ProjectPolicy => ({
  revision: 0,
  strategy: { exploration: "balanced", guidance: "" },
  limits: {
    max_teams: 6,
    parallel_jobs: 2,
    parallel_model_calls: 6,
    cpu_total: 8,
    ram_gb: 28,
    duration_minutes: 180,
  },
});
const modes = [
  {
    id: "focused" as const,
    title: "Развивать лучшее",
    description:
      "Углублять перспективные подходы, новые направления открывать по результатам.",
    icon: Focus,
  },
  {
    id: "balanced" as const,
    title: "Баланс",
    description:
      "Улучшать текущий результат и параллельно проверять новые гипотезы.",
    icon: Scale,
  },
  {
    id: "exploratory" as const,
    title: "Больше новых идей",
    description:
      "Активнее искать разные подходы и менять неработающие направления.",
    icon: Sparkles,
  },
];
const fields: {
  key: Exclude<keyof ProjectPolicy["limits"], "api_budget">;
  label: string;
  hint: string;
  min: number;
  max: number;
  unit: string;
}[] = [
  {
    key: "max_teams",
    label: "Максимум команд",
    hint: "Техлид выбирает число в этих пределах",
    min: 1,
    max: 12,
    unit: "команд",
  },
  {
    key: "parallel_jobs",
    label: "Параллельные вычисления",
    hint: "Одновременно выполняющиеся задания с кодом",
    min: 1,
    max: 32,
    unit: "заданий",
  },
  {
    key: "parallel_model_calls",
    label: "Параллельные запросы к модели",
    hint: "Общий предел для всех агентов проекта",
    min: 1,
    max: 32,
    unit: "запросов",
  },
  {
    key: "duration_minutes",
    label: "Длительность запуска",
    hint: "Общий дедлайн исследования",
    min: 5,
    max: 1440,
    unit: "мин",
  },
  {
    key: "cpu_total",
    label: "CPU",
    hint: "Общий ресурс проекта",
    min: 1,
    max: 64,
    unit: "ядер",
  },
  {
    key: "ram_gb",
    label: "Оперативная память",
    hint: "Общий ресурс проекта",
    min: 1,
    max: 256,
    unit: "ГБ",
  },
];

export default function ProjectSettingsPanel({
  project,
  close,
  saved,
}: {
  project: Project;
  close: () => void;
  saved: () => void;
}) {
  const [tab, setTab] = useState<"strategy" | "limits">("strategy");
  const [policy, setPolicy] = useState(defaults);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (project.demo) {
      try {
        const stored = JSON.parse(
          localStorage.getItem(`dolphinlab-demo-policy:${project.id}`) || "null",
        );
        if (
          stored &&
          ["focused", "balanced", "exploratory"].includes(
            stored.strategy?.exploration,
          )
        )
          setPolicy({
            ...defaults(),
            ...stored,
            strategy: { ...defaults().strategy, ...stored.strategy },
            limits: { ...defaults().limits, ...stored.limits },
          });
      } catch {
        /* An unavailable demo snapshot leaves the known defaults. */
      }
      setLoaded(true);
      setLoading(false);
      return;
    }
    let active = true;
    api
      .projectSettings(project.id)
      .then((value) => {
        if (active) {
          setPolicy(value);
          setLoaded(true);
        }
      })
      .catch((error) => {
        if (active) setError(error.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [project.id, project.demo]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!loaded || busy) return;
    const invalid = fields.find(
      (field) =>
        !Number.isInteger(policy.limits[field.key]) ||
        policy.limits[field.key] < field.min ||
        policy.limits[field.key] > field.max,
    );
    if (invalid) {
      setTab("limits");
      setError(
        `${invalid.label}: укажи целое число от ${invalid.min} до ${invalid.max}.`,
      );
      return;
    }
    setError("");
    setBusy(true);
    try {
      if (project.demo)
        localStorage.setItem(
          `dolphinlab-demo-policy:${project.id}`,
          JSON.stringify({ ...policy, revision: policy.revision + 1 }),
        );
      else await api.saveProjectSettings(project.id, policy);
      saved();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-panel project-settings">
      <span className="eyebrow">{project.name}</span>
      <h2>Настройки проекта</h2>
      <div
        className="settings-tabs"
        role="tablist"
        aria-label="Настройки проекта"
      >
        <button
          role="tab"
          aria-selected={tab === "strategy"}
          className={tab === "strategy" ? "active" : ""}
          onClick={() => setTab("strategy")}
        >
          <Compass size={15} />
          Исследование
        </button>
        <button
          role="tab"
          aria-selected={tab === "limits"}
          className={tab === "limits" ? "active" : ""}
          onClick={() => setTab("limits")}
        >
          <Cpu size={15} />
          Ресурсы и лимиты
        </button>
      </div>
      {loading ? (
        <div className="settings-loading">
          <Loader2 size={20} className="spin" />
        </div>
      ) : (
        <form onSubmit={submit} noValidate>
          <div hidden={tab !== "strategy"} className="policy-strategy">
            <div className="policy-section-title">
              <h3>Поиск новых гипотез</h3>
              <p>Ориентир для техлида. Конкретные подходы выбирает команда.</p>
            </div>
            <div
              className="exploration-options"
              role="radiogroup"
              aria-label="Стратегия исследования"
            >
              {modes.map((mode) => (
                <button
                  type="button"
                  role="radio"
                  aria-checked={policy.strategy.exploration === mode.id}
                  className={`exploration-option ${policy.strategy.exploration === mode.id ? "active" : ""}`}
                  key={mode.id}
                  onClick={() =>
                    setPolicy((before) => ({
                      ...before,
                      strategy: { ...before.strategy, exploration: mode.id },
                    }))
                  }
                >
                  <span className="exploration-icon">
                    <mode.icon size={18} />
                  </span>
                  <span>
                    <strong>{mode.title}</strong>
                    <small>{mode.description}</small>
                  </span>
                  <span className="exploration-radio">
                    {policy.strategy.exploration === mode.id && (
                      <Check size={11} />
                    )}
                  </span>
                </button>
              ))}
            </div>
            <label className="field-label">
              Дополнительные ориентиры
              <textarea
                rows={3}
                maxLength={4000}
                value={policy.strategy.guidance}
                onChange={(event) =>
                  setPolicy((before) => ({
                    ...before,
                    strategy: {
                      ...before.strategy,
                      guidance: event.target.value,
                    },
                  }))
                }
                placeholder="Например: уделить внимание интерпретируемости и устойчивости результата"
              />
            </label>
            <p className="field-help">
              Независимая проверка результатов обязательна при любой стратегии.
            </p>
          </div>
          <div hidden={tab !== "limits"} className="policy-limits">
            <div className="policy-section-title">
              <h3>Границы исполнения</h3>
              <p>
                Планировщик ограничивает загрузку. Руководители распределяют
                работу внутри этих рамок.
              </p>
            </div>
            <div className="policy-limit-fields">
              {fields.map((field) => (
                <label className="policy-limit-row" key={field.key}>
                  <span>
                    <strong>{field.label}</strong>
                    <small>{field.hint}</small>
                  </span>
                  <span className="policy-number">
                    <input
                      type="number"
                      required
                      min={field.min}
                      max={field.max}
                      step={1}
                      aria-label={field.label}
                      value={policy.limits[field.key]}
                      onChange={(event) =>
                        setPolicy((before) => ({
                          ...before,
                          limits: {
                            ...before.limits,
                            [field.key]: Number(event.target.value),
                          },
                        }))
                      }
                    />
                    <span>{field.unit}</span>
                  </span>
                </label>
              ))}
              <label className="policy-limit-row">
                <span>
                  <strong>Лимит API</strong>
                  <small>В валюте тарифа. Пусто — без денежного лимита.</small>
                </span>
                <span className="policy-number">
                  <input
                    type="number"
                    min={0.01}
                    step="any"
                    aria-label="Лимит API"
                    placeholder="—"
                    value={policy.limits.api_budget ?? ""}
                    onChange={(event) =>
                      setPolicy((before) => ({
                        ...before,
                        limits: {
                          ...before.limits,
                          api_budget:
                            event.target.value === ""
                              ? null
                              : Number(event.target.value),
                        },
                      }))
                    }
                  />
                  <span>API</span>
                </span>
              </label>
            </div>
          </div>
          <div className="policy-effective">
            <span className="tiny-dot" />
            {project.demo
              ? "Демо · настройки сохраняются в этом браузере"
              : "Настройки для следующего запуска"}
          </div>
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
              disabled={busy || !loaded}
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
    </div>
  );
}
