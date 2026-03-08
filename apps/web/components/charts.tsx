"use client";

import { Status } from "./ui";

const STATUS_FILL: Record<string, string> = {
  succeeded: "bg-ok",
  dead_lettered: "bg-bad",
  cancelled: "bg-idle",
  retry_scheduled: "bg-warn",
  processing: "bg-busy",
  pending: "bg-rule-strong",
};

/**
 * Part-to-whole bar for delivery states. Status colors are reserved for state and always come with
 * glyph + label in the legend, so identity never depends on color alone.
 */
export function StatusBar({ counts, total, order }: { counts: Record<string, number>; total: number; order: string[] }) {
  const parts = order.filter((s) => (counts[s] ?? 0) > 0);
  return (
    <div className="flex flex-col gap-2">
      <div
        className="flex h-3 w-full gap-[2px] overflow-hidden rounded-sm bg-rule"
        role="img"
        aria-label={parts.map((s) => `${counts[s]} ${s.replace("_", " ")}`).join(", ") || "no deliveries yet"}
      >
        {parts.map((s) => (
          <div
            key={s}
            title={`${s.replace("_", " ")}: ${counts[s]} of ${total} (${Math.round((counts[s] / total) * 100)}%)`}
            className={`${STATUS_FILL[s] ?? "bg-ink-3"} h-full first:rounded-l-sm last:rounded-r-sm`}
            style={{ width: `${(counts[s] / total) * 100}%` }}
          />
        ))}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2">
        {order.map((s) => (
          <li key={s} className="flex items-center gap-1">
            <Status value={s} compact />
            <span>{s.replace("_", " ")}</span>
            <span className="font-mono text-ink">{counts[s] ?? 0}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Single-series horizontal bars: one hue, value labels in text color, hover tooltip on every mark. */
export function BarList({
  rows,
  unit = "",
  empty = "No data yet.",
}: {
  rows: { label: string; value: number; hint?: string }[];
  unit?: string;
  empty?: string;
}) {
  const max = Math.max(0, ...rows.map((r) => r.value));
  if (rows.length === 0 || max === 0) return <p className="text-[12px] text-ink-3">{empty}</p>;
  return (
    <table className="w-full text-[12px]">
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} title={`${r.label}: ${r.value}${unit}${r.hint ? ` (${r.hint})` : ""}`}>
            <th scope="row" className="w-36 py-0.5 pr-2 text-left font-normal text-ink-2">
              {r.label}
            </th>
            <td className="py-0.5">
              <div className="h-2.5 rounded-r-[4px] bg-ink-2" style={{ width: `${Math.max(1, (r.value / max) * 100)}%` }} />
            </td>
            <td className="w-16 py-0.5 pl-2 text-right font-mono text-ink">
              {r.value}
              {unit}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
