import type { ChartData, TableData } from "../../types/reports";
import Markdown from "../../components/Markdown";
import { readableText } from "../../lib/presentation";

const format = (value: number) =>
  value.toLocaleString("ru-RU", { maximumSignificantDigits: 6 });

export default function AnalyticalChart({
  spec,
  table,
}: {
  spec: ChartData;
  table: TableData;
}) {
  const xi = table.columns.findIndex((c) => c.name === spec.x),
    yi = table.columns.findIndex((c) => c.name === spec.y);
  if (xi < 0 || yi < 0 || !["bar", "line", "scatter"].includes(spec.type))
    throw new Error("Неподдерживаемая спецификация графика");
  const valid = table.rows
    .map((r, i) => ({ x: r[xi], y: r[yi], i }))
    .filter(
      (r): r is { x: string | number; y: number; i: number } =>
        r.x !== null && typeof r.y === "number" && Number.isFinite(r.y),
    );
  if (!valid.length)
    return <p className="muted">Для графика нет числовых значений.</p>;
  const scale = Math.max(1, ...valid.map((r) => Math.abs(r.y))),
    low = Math.min(0, ...valid.map((r) => r.y / scale)),
    high = Math.max(0, ...valid.map((r) => r.y / scale)) || 1;
  const numericX =
    spec.type === "scatter" ||
    (spec.type === "line" && table.columns[xi].type === "number");
  const xscale = Math.max(
    1,
    ...valid.map((r) => (typeof r.x === "number" ? Math.abs(r.x) : 0)),
  );
  const xmin = Math.min(...valid.map((r) => Number(r.x) / xscale)),
    xmax = Math.max(...valid.map((r) => Number(r.x) / xscale));
  const x = (r: (typeof valid)[number]) =>
    numericX
      ? xmin === xmax
        ? 385
        : 80 + ((Number(r.x) / xscale - xmin) / (xmax - xmin)) * 600
      : 80 + ((r.i + 0.5) / Math.max(1, table.rows.length)) * 600;
  const y = (v: number) => 245 - ((v / scale - low) / (high - low)) * 210;
  const width = Math.max(1, Math.min(64, 540 / Math.max(1, table.rows.length)));
  const path = valid
    .map(
      (r, i) =>
        (i === 0 || r.i !== valid[i - 1].i + 1 ? "M" : "L") +
        `${x(r)},${y(r.y)}`,
    )
    .join(" ");
  const ticks = valid.filter(
    (_, i) => i % Math.max(1, Math.ceil(valid.length / 6)) === 0,
  );
  return (
    <figure className="analytical-chart">
      <svg
        viewBox="0 0 720 310"
        role="img"
        aria-label={`${spec.y_label} по ${spec.x_label}`}
      >
        <title>{spec.caption}</title>
        {[0, 1, 2, 3, 4].map((i) => {
          const v = low + ((high - low) * i) / 4,
            py = 245 - (i / 4) * 210;
          return (
            <g key={i}>
              <line x1="80" x2="690" y1={py} y2={py} className="chart-grid" />
              <text x="72" y={py + 4} textAnchor="end">
                {format(v * scale)}
              </text>
            </g>
          );
        })}
        <line x1="80" x2="690" y1={y(0)} y2={y(0)} className="chart-axis" />
        {spec.type === "line" && <path d={path} className="chart-line" />}
        {valid.map((r) =>
          spec.type === "bar" ? (
            <rect
              key={r.i}
              x={x(r) - width / 2}
              y={Math.min(y(r.y), y(0))}
              width={width}
              height={Math.max(0.5, Math.abs(y(r.y) - y(0)))}
              className="chart-bar"
            >
              <title>
                {r.x}: {format(r.y)}
              </title>
            </rect>
          ) : (
            <circle
              key={r.i}
              cx={x(r)}
              cy={y(r.y)}
              r={3.5}
              className="chart-point"
            >
              <title>
                {r.x}: {format(r.y)}
              </title>
            </circle>
          ),
        )}
        {ticks.map((r) => (
          <text key={r.i} x={x(r)} y="266" textAnchor="middle">
            {String(r.x).slice(0, 22)}
          </text>
        ))}
        <text x="385" y="296" textAnchor="middle">
          {spec.x_label}
        </text>
        <text x="80" y="18">
          {spec.y_label}
        </text>
      </svg>
      {spec.caption && <figcaption>{readableText(spec.caption)}</figcaption>}
      <details className="selection-details"><summary>О данных графика · {valid.length} точек</summary><Markdown text={table.selection} technical/><p>Наблюдений в выборке: {table.sample_size}</p></details>
    </figure>
  );
}
