"use client";

import { useState } from "react";
import useSWR from "swr";
import { errorText, fetcher } from "@/lib/api";
import { BarList } from "@/components/charts";
import { pct } from "@/components/simulation";
import { ErrorNote, SectionHeader, fmtMs, fmtTime } from "@/components/ui";

interface Overview {
  window: string;
  source: string;
  bucket: string;
  since: string | null;
  definitions: Record<string, string>;
  deliveries: { total: number; by_status: Record<string, number> };
  attempts: number;
  success_rate: number | null;
  dead_letter_rate: number | null;
  retry_rate: number | null;
  latency_ms: { n: number; p50: number | null; p95: number | null; p99: number | null; insufficient: boolean };
  failure_categories: Record<string, number>;
  volume: { bucket: string; success: number; failure: number }[];
  endpoints: { endpoint_id: string; name: string; attempts: number; failed_attempts: number; dead_lettered: number }[];
  attempt_distribution: Record<string, number>;
}

const WINDOWS = [
  ["1h", "Last hour"],
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["all", "All time"],
] as const;
const SOURCES = [
  ["all", "All deliveries"],
  ["manual", "Manual"],
  ["simulation", "Simulations"],
] as const;
const BUCKET_MS: Record<string, number> = { "1 minute": 60_000, "30 minutes": 1_800_000, "6 hours": 21_600_000, "1 day": 86_400_000 };

export default function AnalyticsPage() {
  const [win, setWin] = useState<string>("24h");
  const [source, setSource] = useState<string>("all");
  const { data, error, isLoading } = useSWR<Overview>(`/analytics/overview?window=${win}&source=${source}`, fetcher, { refreshInterval: 10_000 });

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[18px] font-semibold">Analytics</h1>
      <div className="flex flex-wrap items-center gap-4" role="group" aria-label="Filters">
        <Segmented label="Time window" options={WINDOWS} value={win} onChange={setWin} />
        <Segmented label="Source" options={SOURCES} value={source} onChange={setSource} />
      </div>
      {error ? <ErrorNote>{errorText(error)}</ErrorNote> : null}
      {isLoading && !data ? <p className="text-[13px] text-ink-3">Loading analytics…</p> : null}
      {data && data.deliveries.total === 0 && data.attempts === 0 ? (
        <p className="py-6 text-[13px] text-ink-2">
          No deliveries in this window. Send an event or run a simulation. Every figure here is computed from your
          recorded deliveries and attempts, so there is nothing to show yet.
        </p>
      ) : null}
      {data && (data.deliveries.total > 0 || data.attempts > 0) ? <Body d={data} /> : null}
    </div>
  );
}

function Segmented({ label, options, value, onChange }: { label: string; options: readonly (readonly [string, string])[]; value: string; onChange: (v: string) => void }) {
  return (
    <fieldset className="flex items-center gap-2">
      <legend className="sr-only">{label}</legend>
      <span aria-hidden className="text-[12px] text-ink-3">
        {label}
      </span>
      <div className="flex rounded-sm border border-rule-strong">
        {options.map(([v, l]) => (
          <label key={v} className={`cursor-pointer px-2 py-1 text-[12px] ${value === v ? "bg-panel font-semibold text-ink" : "text-ink-2 hover:text-ink"}`}>
            <input type="radio" className="sr-only" name={label} value={v} checked={value === v} onChange={() => onChange(v)} />
            {l}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function Body({ d }: { d: Overview }) {
  const stats: [string, string, string][] = [
    ["Deliveries", String(d.deliveries.total), d.definitions.window],
    ["Success rate", pct(d.success_rate), d.definitions.success_rate],
    ["Dead-letter rate", pct(d.dead_letter_rate), d.definitions.dead_letter_rate],
    ["Retry rate", pct(d.retry_rate), d.definitions.retry_rate],
    ["Attempts", String(d.attempts), "HTTP attempts made in the window."],
    ["Attempt p95", d.latency_ms.insufficient ? "n/a" : fmtMs(d.latency_ms.p95 ?? 0), d.definitions.latency],
  ];
  return (
    <>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
        {stats.map(([k, v, hint]) => (
          <div key={k}>
            <dt className="text-[12px] text-ink-3">{k}</dt>
            <dd className="font-mono text-[18px] leading-tight">{v}</dd>
            <dd className="mt-0.5 text-[11px] text-ink-3">{hint}</dd>
          </div>
        ))}
      </dl>

      <section className="flex flex-col gap-2">
        <SectionHeader title="Attempts over time" />
        <VolumeChart d={d} />
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="flex flex-col gap-2">
          <SectionHeader title="Attempt outcomes" />
          <BarList rows={Object.entries(d.failure_categories).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label: label.replace("_", " "), value }))} />
        </section>
        <section className="flex flex-col gap-2">
          <SectionHeader title="Attempts per finished delivery" />
          <BarList rows={Object.entries(d.attempt_distribution).map(([k, v]) => ({ label: `${k} attempt${k === "1" ? "" : "s"}`, value: v }))} empty="No finished deliveries yet." />
        </section>
      </div>

      <section className="flex flex-col gap-2">
        <SectionHeader title="By endpoint" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[480px] border-collapse text-[13px]">
            <thead>
              <tr className="text-left text-[12px] text-ink-3">
                <th className="py-2 pr-3 font-medium">Endpoint</th>
                <th className="py-2 pr-3 text-right font-medium">Attempts</th>
                <th className="py-2 pr-3 text-right font-medium">Failed attempts</th>
                <th className="py-2 pr-3 text-right font-medium">Attempt failure rate</th>
                <th className="py-2 text-right font-medium">Dead-lettered</th>
              </tr>
            </thead>
            <tbody>
              {d.endpoints.map((e) => (
                <tr key={e.endpoint_id} className="border-t border-rule">
                  <td className="py-1.5 pr-3">{e.name}</td>
                  <td className="py-1.5 pr-3 text-right font-mono">{e.attempts}</td>
                  <td className="py-1.5 pr-3 text-right font-mono">{e.failed_attempts}</td>
                  <td className="py-1.5 pr-3 text-right font-mono">{e.attempts ? pct(e.failed_attempts / e.attempts) : "n/a"}</td>
                  <td className="py-1.5 text-right font-mono">{e.dead_lettered}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

/** Stacked bars per time bucket. Success/failure use the reserved status colors, with a glyph legend and a table view. */
function VolumeChart({ d }: { d: Overview }) {
  const step = BUCKET_MS[d.bucket] ?? 3_600_000;
  const byTime = new Map(d.volume.map((v) => [new Date(v.bucket).getTime(), v]));
  const times = [...byTime.keys()];
  const end = Math.floor(Date.now() / step) * step;
  const start = d.since ? Math.floor(new Date(d.since).getTime() / step) * step : times.length ? Math.min(...times) : end;
  const buckets: { t: number; success: number; failure: number }[] = [];
  for (let t = start; t <= end && buckets.length < 400; t += step) {
    const v = byTime.get(t);
    buckets.push({ t, success: v?.success ?? 0, failure: v?.failure ?? 0 });
  }
  const max = Math.max(1, ...buckets.map((b) => b.success + b.failure));
  const label = (t: number) => (step >= 86_400_000 ? new Date(t).toLocaleDateString() : fmtTime(new Date(t).toISOString()));
  return (
    <div className="flex flex-col gap-2">
      <ul className="flex gap-4 text-[12px] text-ink-2">
        <li className="flex items-center gap-1">
          <span aria-hidden className="text-ok">●</span> Successful attempts
        </li>
        <li className="flex items-center gap-1">
          <span aria-hidden className="text-bad">✕</span> Failed attempts
        </li>
        <li className="text-ink-3">Bucket: {d.bucket}</li>
      </ul>
      <div className="flex h-32 items-end gap-px border-b border-rule" role="img" aria-label={`Attempts per ${d.bucket}; see the table below for values.`}>
        {buckets.map((b) => (
          <div
            key={b.t}
            className="flex h-full min-w-0 flex-1 flex-col justify-end"
            title={`${label(b.t)}: ${b.success} successful, ${b.failure} failed`}
          >
            {b.failure > 0 ? <div className="rounded-t-[2px] bg-bad" style={{ height: `${(b.failure / max) * 100}%` }} /> : null}
            {b.success > 0 ? <div className={`bg-ok ${b.failure > 0 ? "mt-[2px]" : "rounded-t-[2px]"}`} style={{ height: `${(b.success / max) * 100}%` }} /> : null}
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[11px] text-ink-3">
        <span>{buckets.length ? label(buckets[0].t) : ""}</span>
        <span>{buckets.length ? label(buckets[buckets.length - 1].t) : ""}</span>
      </div>
      <details>
        <summary className="cursor-pointer text-[12px] text-ink-2">Show as table</summary>
        <table className="mt-1 text-[12px]">
          <thead>
            <tr className="text-left text-ink-3">
              <th className="pr-4 font-medium">Bucket start</th>
              <th className="pr-4 text-right font-medium">Successful</th>
              <th className="text-right font-medium">Failed</th>
            </tr>
          </thead>
          <tbody>
            {d.volume.map((v) => (
              <tr key={v.bucket}>
                <td className="pr-4">{new Date(v.bucket).toLocaleString()}</td>
                <td className="pr-4 text-right font-mono">{v.success}</td>
                <td className="text-right font-mono">{v.failure}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
