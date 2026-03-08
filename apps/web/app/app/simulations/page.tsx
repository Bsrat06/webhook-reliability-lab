"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { errorText, fetcher } from "@/lib/api";
import type { Preset, SimulationRun } from "@/lib/types";
import { Button, ErrorNote, Mono, SectionHeader, fmtDateTime, fmtMs } from "@/components/ui";
import { RunStatus, pct } from "@/components/simulation";

export default function SimulationsPage() {
  const router = useRouter();
  const { data, error } = useSWR<SimulationRun[]>("/simulations", fetcher, {
    refreshInterval: (d) => (d?.some((r) => r.status === "running") ? 2000 : 0),
  });
  const { data: presets } = useSWR<Preset[]>("/simulations/presets", fetcher);
  const [picked, setPicked] = useState<string[]>([]);
  const comparable = (r: SimulationRun) => r.status !== "running";

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[18px] font-semibold">Simulation lab</h1>
        <div className="flex gap-2">
          <Button disabled={picked.length < 2} onClick={() => router.push(`/app/simulations/compare?ids=${picked.join(",")}`)}>
            Compare selected ({picked.length})
          </Button>
          <Link href="/app/simulations/new" className="inline-flex h-8 items-center rounded-sm border border-accent bg-accent px-3 text-[13px] font-medium text-accent-ink hover:brightness-110">
            New simulation
          </Link>
        </div>
      </div>
      <p className="max-w-prose text-[13px] text-ink-2">
        A simulation sends real events through the delivery engine to your built-in receivers. Each run gets a
        seeded failure profile: the same seed and settings reproduce the same failure decisions. Results are
        measured from the stored deliveries and attempts.
      </p>

      {error ? <ErrorNote>{errorText(error)}</ErrorNote> : null}
      {!data && !error ? <p className="text-[13px] text-ink-3">Loading runs…</p> : null}

      {data && data.length === 0 ? (
        <section className="flex flex-col gap-3">
          <SectionHeader title="Start with a scenario" />
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {presets?.map((p) => (
              <li key={p.key}>
                <Link href={`/app/simulations/new?preset=${p.key}`} className="flex h-full flex-col gap-1 rounded-md border border-rule bg-panel p-3 hover:border-ink-3">
                  <span className="text-[13px] font-semibold">{p.title}</span>
                  <span className="text-[12px] text-ink-2">{p.description}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {data && data.length > 0 ? (
        <section className="flex flex-col gap-2">
          <SectionHeader title="Runs" />
          <p className="text-[12px] text-ink-3">Select two to four finished runs to compare them side by side.</p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-[13px]">
              <thead>
                <tr className="text-left text-[12px] text-ink-3">
                  <th className="w-8 py-2 pr-2 font-medium">
                    <span className="sr-only">Compare</span>
                  </th>
                  <th className="py-2 pr-3 font-medium">Run</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 text-right font-medium">Deliveries</th>
                  <th className="py-2 pr-3 text-right font-medium">Success</th>
                  <th className="py-2 pr-3 text-right font-medium">Dead-lettered</th>
                  <th className="py-2 pr-3 text-right font-medium">Attempts</th>
                  <th className="py-2 pr-3 text-right font-medium">Duration</th>
                  <th className="py-2 font-medium">Started</th>
                </tr>
              </thead>
              <tbody>
                {data.map((r) => {
                  const s = r.summary;
                  return (
                    <tr key={r.id} className="border-t border-rule hover:bg-panel">
                      <td className="py-2 pr-2">
                        <input
                          type="checkbox"
                          aria-label={`Select ${r.name} for comparison`}
                          disabled={!comparable(r) || (!picked.includes(r.id) && picked.length >= 4)}
                          checked={picked.includes(r.id)}
                          onChange={(e) => setPicked((p) => (e.target.checked ? [...p, r.id] : p.filter((x) => x !== r.id)))}
                        />
                      </td>
                      <td className="py-2 pr-3">
                        <Link href={`/app/simulations/${r.id}`} className="font-medium hover:underline">
                          {r.name}
                        </Link>
                        <div className="text-[12px] text-ink-3">
                          seed <Mono>{r.random_seed}</Mono> · max {r.configuration.retry_policy.max_attempts} attempts
                        </div>
                      </td>
                      <td className="py-2 pr-3">
                        <RunStatus run={r} />
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">{r.delivery_count}</td>
                      <td className="py-2 pr-3 text-right font-mono">{pct(s?.success_rate)}</td>
                      <td className="py-2 pr-3 text-right font-mono">{s?.by_status.dead_lettered ?? 0}</td>
                      <td className="py-2 pr-3 text-right font-mono">{s?.attempts ?? 0}</td>
                      <td className="py-2 pr-3 text-right font-mono">{s ? fmtMs(s.elapsed_ms) : "—"}</td>
                      <td className="py-2 text-ink-2">{fmtDateTime(r.started_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}
