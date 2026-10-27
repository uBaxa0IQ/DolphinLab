import { CheckCircle2, ChevronRight, Circle, Loader2 } from "lucide-react";
import type { Experiment } from "../../types";

export default function ExperimentRows({
  rows,
  open,
}: {
  rows: Experiment[];
  open: (experiment: Experiment) => void;
}) {
  return (
    <div className="experiment-list">
      <div className="experiment-head">
        <span>Эксперимент</span>
        <span>Результат</span>
        <span>Время</span>
        <span />
      </div>
      {rows.map((exp) => (
        <button
          className="experiment-row"
          key={exp.id}
          onClick={() => open(exp)}
        >
          <span className={`experiment-status ${exp.status}`}>
            {exp.status === "running" ? (
              <Loader2 className="spin" size={17} />
            ) : exp.status === "verified" ? (
              <CheckCircle2 size={17} />
            ) : (
              <Circle size={17} />
            )}
          </span>
          <span className="experiment-name">
            <strong>{exp.name}</strong>
            <small>
              {exp.id} <span>·</span> {exp.team}
            </small>
          </span>
          <span
            className={`experiment-score ${exp.status === "verified" ? "scored" : ""}`}
          >
            {exp.score}
            {exp.status === "running" && <small>Обучается</small>}
          </span>
          <span className="experiment-duration">{exp.duration}</span>
          <ChevronRight size={15} />
        </button>
      ))}
    </div>
  );
}
