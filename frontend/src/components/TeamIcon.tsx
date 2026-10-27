import { Layers3, ShieldCheck, Sparkles } from "lucide-react";
import type { Team } from "../types";

export default function TeamIcon({
  type,
  size: px = 19,
}: {
  type: Team["icon"];
  size?: number;
}) {
  return type === "layers" ? (
    <Layers3 size={px} />
  ) : type === "shield" ? (
    <ShieldCheck size={px} />
  ) : (
    <Sparkles size={px} />
  );
}
