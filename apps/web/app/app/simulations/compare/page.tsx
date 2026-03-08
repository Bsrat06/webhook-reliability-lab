"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import useSWR from "swr";
import { errorText, post } from "@/lib/api";
import type { Comparison, SimulationRun, SimulationSummary } from "@/lib/types";
import { StatusBar } from "@/components/charts";
import { DELIVERY_ORDER, RunStatus, pct } from "@/components/simulation";
import { ErrorNote, Mono, SectionHeader, fmtMs } from "@/components/ui";

export default function ComparePage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-3">Loading…</p>}>
      <Compare />
    </Suspense>
  );
}

type Metric = {
  label: string;
  value: (s: SimulationSummary) => number | null;
  format: (v: number) => string;
  better: "higher" | "lower" | null;
};

const METRICS: Metric[] = [
  { label: "Deliveries", value: (s) => s.deliveries, format: String, better: null },
  { label: "Succeeded", value: (s) => s.by_status.succeeded, format: String, better: null },
  { label: "Dead-lettered", value: (s) => s.by_status.dead_lettered, format: String, better: null },
  { label: "Cancelled", value: (s) => s.by_status.cancelled, format: String, better: null },
  { label: "Success rate", value: (s) => s.success_rate, format: (v) => pct(v), better: "higher" },
  { label: "Dead-letter rate", value: (s) => s.dead_letter_rate, format: (v) => pct(v), better: "lower" },
  { label: "Attempts", value: (s) => s.attempts, format: String, better: null },
  { label: "Retries", value: (s) => s.retries, format: String, better: null },
  { label: "Attempts per delivery", value: (s) => s.attempts_per_delivery, format: (v) => v.toFixed(2), better: "lower" },
  { label: "Duplicates suppressed", value: (s) => s.duplicates_suppressed, format: String, better: null },
  { label: "Attempt latency p50", value: (s) => s.latency_ms.p50, format: fmtMs, better: "lower" },
  { label: "Attempt latency p95", value: (s) => s.latency_ms.p95, format: fmtMs, better: "lower" },
  { label: "Created → delivered p50", value: (s) => s.delivery_time_ms.p50, format: fmtMs, better: "lower" },
  { label: "Created → delivered p95", value: (s) => s.delivery_time_ms.p95, format: fmtMs, better: "lower" },
  { label: "Total run time", value: (s) => s.elapsed_ms, format: fmtMs, better: "lower" },
];

function Compare() {
  const ids = (useSearchParams().get("ids") ?? "").split(",").filter(Boolean);
  const { data, error } = useSWR<Comparison>(ids.length >= 2 ? ["compare", ...ids] : null, () => post<Comparison>("/simulations/compare", { run_ids: ids }));

  if (ids.length < 2) return <ErrorNote>Select at least two runs on the Simulations page to compare them.</ErrorNote>;
  if (error) return <ErrorNote>{errorText(error)}</ErrorNote>;
  if (!data) return <p className="text-[13px] text-ink-3">Loading comparison…</p>;

  const runs = data.runs;
  const diffKeys = data.config_differences.map((d) => d.key);
  const seedsDiffer = diffKeys.includes("seed");
  const behaviorDiffers = diffKeys.some((k) => k.startsWith("receiver.") || k === "event_count" || k === "duplicate_send_rate");
  const small = runs.some((r) => (r.summary?.deliveries ?? 0) < 100);

  return (
    <div className="flex flex-col gap-6">
      <nav className="text-[12px] text-ink-3">
        <Link href="/app/simulations" className="hover:text-ink">
          Simulations
        </Link>{" "}
        / Compare
      </nav>
      <h1 className="text-[18px] font-semibold">Compare {runs.length} runs</h1>

      <section className="flex flex-col gap-1 border-l-2 border-rule-strong pl-3 text-[13px] text-ink-2" aria-label="How to read this comparison">
        {!seedsDiffer && !behaviorDiffers ? (
          <p>
            Same seed and receiver behavior, so every run saw the same failure decisions. Differences in outcome come
            from the settings that differ below{diffKeys.length ? "" : " (none: these runs are repeats)"}, plus timing.
          </p>
        ) : (
          <p>
            {seedsDiffer ? "The seeds differ, so the runs saw different failure decisions. " : ""}
            {behaviorDiffers ? "The receiver behavior or workload differs too. " : ""}
            Part of any difference below comes from those inputs, not only from the retry policy. To isolate a policy
            change, rerun with the same seed.
          </p>
        )}
        {small ? <p>At least one run has fewer than 100 deliveries. Treat small differences as noise; this comparison makes no claim of statistical significance.</p> : null}
      </section>

      <section className="flex flex-col gap-2">
        <SectionHeader title="Outcomes" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-[13px]">
            <thead>
              <RunHeader runs={runs} />
            </thead>
            <tbody>
              <tr className="border-t border-rule">
                <th scope="row" className="py-2 pr-3 text-left font-normal text-ink-2">
                  Delivery states
                </th>
                {runs.map((r) => (
                  <td key={r.id} className="py-2 pr-3 align-top">
                    {r.summary ? <StatusBar counts={r.summary.by_status} total={r.summary.deliveries} order={DELIVERY_ORDER.slice(0, 3)} /> : "—"}
                  </td>
                ))}
              </tr>
              {METRICS.map((m) => {
                const vals = runs.map((r) => (r.summary ? m.value(r.summary) : null));
                const present = vals.filter((v): v is number => v !== null);
                const best =
                  m.better && present.length === runs.length && new Set(present).size > 1
                    ? m.better === "higher"
                      ? Math.max(...present)
                      : Math.min(...present)
                    : null;
                return (
                  <tr key={m.label} className="border-t border-rule">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink-2">
                      {m.label}
                    </th>
                    {vals.map((v, i) => (
                      <td key={runs[i].id} className="py-1.5 pr-3 font-mono">
                        {v === null ? <span className="text-ink-3">n/a</span> : m.format(v)}
                        {best !== null && v === best ? (
                          <span className="ml-2 font-sans text-[11px] text-ok">▲ best</span>
                        ) : null}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-[12px] text-ink-3">
          n/a means not enough data: rates need finished deliveries; latency percentiles need at least 20 samples.
        </p>
      </section>

      <section className="flex flex-col gap-2">
        <SectionHeader title="Configuration differences" />
        {data.config_differences.length === 0 ? (
          <p className="text-[13px] text-ink-2">The configurations are identical (apart from target receiver IDs).</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] border-collapse text-[13px]">
              <thead>
                <RunHeader runs={runs} />
              </thead>
              <tbody>
                {data.config_differences.map((d) => (
                  <tr key={d.key} className="border-t border-rule">
                    <th scope="row" className="py-1.5 pr-3 text-left font-mono text-[12px] font-normal text-ink-2">
                      {d.key}
                    </th>
                    {d.values.map((v, i) => (
                      <td key={runs[i].id} className="py-1.5 pr-3 font-mono text-[12px]">
                        {v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function RunHeader({ runs }: { runs: SimulationRun[] }) {
  return (
    <tr className="text-left text-[12px] text-ink-3">
      <th className="w-48 py-2 pr-3 font-medium">
        <span className="sr-only">Metric</span>
      </th>
      {runs.map((r) => (
        <th key={r.id} scope="col" className="py-2 pr-3 align-bottom font-medium">
          <Link href={`/app/simulations/${r.id}`} className="block text-[13px] text-ink hover:underline">
            {r.name}
          </Link>
          <span className="flex items-center gap-2">
            <RunStatus run={r} /> seed <Mono>{r.random_seed}</Mono>
          </span>
        </th>
      ))}
    </tr>
  );
}
