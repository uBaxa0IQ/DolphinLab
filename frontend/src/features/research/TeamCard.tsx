import { ArrowUpRight } from "lucide-react";
import TeamIcon from "../../components/TeamIcon";
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
      <h3>{team.name}</h3>
      <p className="team-focus">{team.focus}</p>
      <div className="team-stage">
        <span className={`tiny-dot ${team.state}`} />
        {team.stage}
      </div>
      <p className="team-description">{team.current}</p>
      <div className="team-card-bottom">
        <div className="avatar-stack">
          {team.members.map((m) => (
            <span
              key={m.name}
              className={`avatar ${m.color}`}
              title={`${m.name} · ${m.role}`}
            >
              {m.name[0]}
            </span>
          ))}
        </div>
        <span className="team-updated">{team.updated}</span>
      </div>
    </button>
  );
}
