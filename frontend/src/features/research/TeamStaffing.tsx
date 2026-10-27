import { ShieldCheck } from "lucide-react";
import type { Team } from "../../types";

const roles: Record<string, string> = {
  tech_lead: "Техлид",
  team_lead: "Тимлид",
  ml_engineer: "ML-инженер",
  data_engineer: "Инженер данных",
  data_analyst: "Аналитик данных",
  evaluator: "Независимый проверяющий",
  report_editor: "Редактор отчёта",
};
const states: Record<string, string> = {
  active: "в работе",
  completed: "завершено",
  superseded: "заменено",
  interrupted: "прервано",
};
const activities: Record<string, string> = {
  ready: "Готов к работе",
  waiting: "Ожидает события",
  running: "Работает",
  closed: "Сессия закрыта",
  blocked: "Нужна помощь",
};
const reasons: Record<string, string> = {
  "initial assignment": "Первое назначение",
  "lead reassignment": "Новое назначение от руководителя",
  "charter revised": "Изменена задача команды",
  "fresh context episode": "Новый эпизод работы",
  "legacy import": "Восстановлено прежнее назначение",
};

export default function TeamStaffing({ team }: { team: Team }) {
  return (
    <>
      <h3 className="member-heading">
        Участники <span>{team.members.length}</span>
      </h3>
      <div className="member-list">
        {team.members.map((m) => (
          <div key={m.name} className="member-row">
            <span className={`avatar ${m.color}`}>{m.name[0]}</span>
            <div>
              <strong>
                {roles[m.role] || m.name}
                <span title={m.name}>
                  {roles[m.role] ? m.name.slice(-8) : m.role}
                </span>
              </strong>
              <p>{activities[m.state] || m.state}</p>
              {m.profile_revision && (
                <p>
                  Профиль v{m.profile_revision} · назначение{" "}
                  {m.assignment_revision}
                </p>
              )}
              {m.assignment && <p>{m.assignment}</p>}
              {m.assignment_state && (
                <p>
                  Назначение: {states[m.assignment_state] || m.assignment_state}
                </p>
              )}
              {m.handover && (
                <details>
                  <summary>Передача результатов</summary>
                  <p>{m.handover.summary}</p>
                  {m.handover.results.map((item, i) => (
                    <p key={i}>{item}</p>
                  ))}
                  {m.handover.evidence.map((item, i) => (
                    <p key={i}>Источник: {item}</p>
                  ))}
                  {m.handover.open_questions.map((item, i) => (
                    <p key={i}>Открытый вопрос: {item}</p>
                  ))}
                  {m.handover.unsuccessful_approaches?.map((item, i) => (
                    <p key={i}>Неудачный подход: {item}</p>
                  ))}
                  {m.handover.next_actions?.map((item, i) => (
                    <p key={i}>Далее: {item}</p>
                  ))}
                </details>
              )}
            </div>
            <span className={`tiny-dot ${team.state}`} />
          </div>
        ))}
      </div>
      <div className="team-modal-footer">
        <ShieldCheck size={15} />
        {team.reviewer_id
          ? "Назначен evaluator; проверка требует отдельного результата"
          : team.members.some(
                (m) =>
                  m.role === "evaluator" ||
                  m.role.toLowerCase().includes("evaluator"),
              )
            ? "В составе есть evaluator; статус проверки указан у результата"
            : "Независимый проверяющий подключается отдельно при необходимости"}
      </div>
      {!!team.staffing_history?.length && (
        <details className="staffing-history">
          <summary>История состава и назначений</summary>
          {team.staffing_history.map((item) => (
            <p key={item.id}>
              {new Date(item.created * 1000).toLocaleString("ru-RU")} ·{" "}
              {reasons[item.reason] || item.reason}
            </p>
          ))}
        </details>
      )}
    </>
  );
}
