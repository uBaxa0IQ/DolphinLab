import {
  ChevronDown,
  History,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import Badge from "../../components/Badge";
import { api } from "../../lib/api";
import type {
  GoalRevision,
  GoalSpec,
  Project,
  ResearchIteration,
} from "../../types";
import { statusLabels } from "./RunPanel";
import ProjectMemory from "./ProjectMemory";

const terminal = new Set(["completed", "stopped", "failed"]);
const lines = (value: string) =>
  value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
const date = (value: string) =>
  new Date(value).toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

export default function ResearchHistory({
  project,
  expanded,
  enabled,
  changed,
}: {
  project: Project;
  expanded: boolean;
  enabled: boolean;
  changed: () => void;
}) {
  const [goals, setGoals] = useState<GoalRevision[]>([]);
  const [iterations, setIterations] = useState<ResearchIteration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [draft, setDraft] = useState<GoalSpec | null>(null);
  const [baseRevision, setBaseRevision] = useState(0);
  const [outputs, setOutputs] = useState("");
  const [constraints, setConstraints] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([api.goals(project.id), api.iterations(project.id)])
      .then(([nextGoals, nextIterations]) => {
        if (active) {
          setGoals(nextGoals);
          setIterations(nextIterations);
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
  }, [
    project.id,
    project.current_goal?.id,
    project.latest_run?.updated,
    refresh,
  ]);

  // Scope drafts and cached history to this project when the user switches projects.
  useEffect(() => {
    setDraft(null);
    setGoals([]);
    setIterations([]);
    setError("");
  }, [project.id]);

  const current = goals[0] || project.current_goal;
  const activeRun =
    project.latest_run && !terminal.has(project.latest_run.status);

  function edit() {
    if (!current) return;
    setDraft(current.goal);
    setBaseRevision(current.revision);
    setOutputs(current.goal.expected_outputs.join("\n"));
    setConstraints(current.goal.constraints.join("\n"));
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || busy) return;
    const expectedOutputs = lines(outputs);
    const goalConstraints = lines(constraints);
    if (
      [expectedOutputs, goalConstraints].some(
        (items) =>
          items.length > 30 || items.some((item) => item.length > 4000),
      )
    ) {
      setError(
        "В каждом списке допустимо до 30 пунктов, каждый не длиннее 4000 символов.",
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.createGoal(project.id, baseRevision, {
        ...draft,
        expected_outputs: expectedOutputs,
        constraints: goalConstraints,
      });
      setDraft(null);
      setRefresh((value) => value + 1);
      changed();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function prepare() {
    if (!current || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.createIteration(project.id, current.id, iterations[0]);
      setRefresh((value) => value + 1);
      changed();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function start(iteration: ResearchIteration) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.start(project.id, iteration.id, project.latest_run?.id);
      setRefresh((value) => value + 1);
      changed();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="research-history"
      aria-label="Цель и итерации исследования"
    >
      <div className="research-heading">
        <div>
          <span className="eyebrow">ЦЕЛЬ ИССЛЕДОВАНИЯ</span>
          <h2>
            {current ? `Версия ${current.revision}` : "Постановка задачи"}
          </h2>
        </div>
        <button
          className="button secondary small"
          onClick={edit}
          disabled={!current || busy || loading || project.archived || !!draft}
        >
          <Pencil size={13} /> Изменить цель
        </button>
      </div>
      {current && !draft && (
        <>
          <p className="research-question">{current.goal.question}</p>
          {(current.goal.expected_outputs.length > 0 ||
            current.goal.constraints.length > 0 ||
            Object.keys(current.goal.definitions).length > 0 ||
            current.goal.open_questions.length > 0) && (
            <details className="research-conditions">
              <summary>Результат и условия</summary>
              {current.goal.expected_outputs.map((item, i) => (
                <p key={`output-${i}`}>
                  <strong>Результат:</strong> {item}
                </p>
              ))}
              {Object.entries(current.goal.definitions).map(([name, value]) => (
                <p key={name}>
                  <strong>{name}:</strong> {value}
                </p>
              ))}
              {current.goal.constraints.map((item, i) => (
                <p key={`constraint-${i}`}>
                  <strong>Условие:</strong> {item}
                </p>
              ))}
              {current.goal.open_questions.map((item, i) => (
                <p key={`question-${i}`}>
                  <strong>Открытый вопрос:</strong> {item}
                </p>
              ))}
            </details>
          )}
          <p className="research-hint">
            Изменения цели относятся к будущим запускам. В истории сохраняется
            постановка каждого исследования.
          </p>
        </>
      )}
      {draft && (
        <form className="research-goal-form" onSubmit={save}>
          <label>
            Вопрос исследования
            <textarea
              required
              maxLength={20000}
              rows={4}
              value={draft.question}
              onChange={(event) =>
                setDraft({ ...draft, question: event.target.value })
              }
            />
          </label>
          <label>
            Ожидаемый результат
            <textarea
              rows={2}
              value={outputs}
              maxLength={30000}
              placeholder="Каждый пункт с новой строки"
              onChange={(event) => setOutputs(event.target.value)}
            />
          </label>
          <label>
            Ограничения
            <textarea
              rows={2}
              value={constraints}
              maxLength={30000}
              placeholder="Период, область анализа и другие условия"
              onChange={(event) => setConstraints(event.target.value)}
            />
          </label>
          <p className="research-hint">
            Сохранение цели не запускает модель и вычисления.
          </p>
          <div className="research-actions">
            <button
              className="button secondary small"
              type="button"
              disabled={busy}
              onClick={() => setDraft(null)}
            >
              Отмена
            </button>
            <button
              className="button primary small"
              disabled={busy || !draft.question.trim()}
            >
              {busy && <Loader2 size={13} className="spin" />} Сохранить новую
              версию
            </button>
          </div>
        </form>
      )}
      {loading && (
        <p className="research-hint" role="status">
          <Loader2 size={13} className="spin" /> Обновление истории…
        </p>
      )}
      {error && (
        <div className="research-error" role="alert">
          <p>{error}</p>
          <button
            className="button secondary small"
            disabled={loading || busy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw size={13} /> Обновить историю
          </button>
        </div>
      )}
      {expanded && (
        <>
          <ProjectMemory
            projectId={project.id}
            eventId={project.events[0]?.id}
          />
          <div className="research-heading research-iterations-heading">
            <h3>
              <History size={15} /> Итерации
            </h3>
            <button
              className="button secondary small"
              disabled={
                !current || loading || busy || project.archived || !!draft
              }
              onClick={() => void prepare()}
            >
              <Plus size={13} /> Подготовить итерацию
            </button>
          </div>
          {!loading && !iterations.length && !error && (
            <p className="research-hint">
              Подготовь итерацию для текущей цели. Первый запуск также создаст
              её автоматически.
            </p>
          )}
          <div className="research-iterations">
            {iterations.map((iteration) => (
              <details key={iteration.id}>
                <summary>
                  <span className="iteration-number">{iteration.sequence}</span>
                  <span className="iteration-title">
                    Итерация {iteration.sequence}
                    <small>
                      Цель v{iteration.goal.revision} ·{" "}
                      {date(iteration.created)}
                      {iteration.legacy ? " · из истории запусков" : ""}
                    </small>
                  </span>
                  <Badge>
                    {iteration.status === "draft"
                      ? "Подготовлена"
                      : statusLabels[iteration.status] || iteration.status}
                  </Badge>
                  <ChevronDown
                    className="iteration-chevron"
                    size={14}
                    aria-hidden="true"
                  />
                </summary>
                <div className="iteration-content">
                  <p>{iteration.goal.goal.question}</p>
                  {iteration.parent_iteration_id && (
                    <p className="research-hint">
                      Продолжает итерацию{" "}
                      {iterations.find(
                        (item) => item.id === iteration.parent_iteration_id,
                      )?.sequence || "из истории"}
                      .
                    </p>
                  )}
                  {iteration.runs.map((run) => (
                    <p className="iteration-run" key={run.id}>
                      Запуск {run.id.slice(0, 8)}{" "}
                      <span>{statusLabels[run.status] || run.status}</span>
                    </p>
                  ))}
                  {!iteration.runs.length && (
                    <button
                      className="button primary small"
                      disabled={
                        busy || !enabled || !!activeRun || project.archived
                      }
                      onClick={() => void start(iteration)}
                    >
                      Запустить эту итерацию
                    </button>
                  )}
                </div>
              </details>
            ))}
          </div>
          {goals.length > 1 && (
            <details className="research-goal-versions">
              <summary>Предыдущие версии цели · {goals.length - 1}</summary>
              {goals.slice(1).map((goal) => (
                <div key={goal.id}>
                  <strong>Версия {goal.revision}</strong>
                  <small>{date(goal.created)}</small>
                  <p>{goal.goal.question}</p>
                </div>
              ))}
            </details>
          )}
        </>
      )}
    </section>
  );
}
