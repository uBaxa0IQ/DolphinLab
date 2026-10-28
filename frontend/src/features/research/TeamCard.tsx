import { ArrowUpRight } from "lucide-react";
import TeamIcon from "../../components/TeamIcon";
import { shortText } from "../../lib/presentation";
import type { Team } from "../../types";

export default function TeamCard({
  team,
  open,
}: {
  team: Team;
  open: () => void;
}) {
  return (
    <button className={`team-card ${team.state}`} onClick={open}>
      <div className="team-card-top">
        <span className={`team-symbol ${team.icon}`}>
          <TeamIcon type={team.icon} />
        </span>
        <span className={`state-dot ${team.state}`} />
        <span className="team-open">
          <ArrowUpRight size={17} />
        </span>
      </div>
      <h3>{shortText(team.name, 110)}</h3>
      <p className="team-focus">{shortText(team.focus, 120)}</p>
      <div className="team-stage">
        <span className={`tiny-dot ${team.state}`} />
        {team.state === "closed"
          ? "Работа завершена"
          : team.state === "running"
            ? "В работе"
            : "Ожидание"}
      </div>

      <div className="team-card-bottom">
        <span className="muted">Специалистов: {team.members.length}</span>
        <span className="team-updated">{team.updated}</span>
      </div>
    </button>
  );
}
