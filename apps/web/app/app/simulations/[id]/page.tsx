"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import useSWRInfinite from "swr/infinite";
import { ApiError, errorText, fetcher, post } from "@/lib/api";
import type { EventList } from "@/lib/types";
import { useLiveSimulation, type Transport } from "@/lib/use-live-simulation";
import { ConfigSummary, RunStatus, SummaryPanel } from "@/components/simulation";
import { Button, ErrorNote, Mono, SectionHeader, Status, fmtDateTime, fmtMs, shortId } from "@/components/ui";

const TRANSPORT: Record<Transport, { label: string; cls: string }> = {
  connecting: { label: "Connecting…", cls: "text-ink-3" },
  live: { label: "Live (server-sent events)", cls: "text-ok" },
  reconnecting: { label: "Connection lost, reconnecting…", cls: "text-warn" },
  polling: { label: "Live connection unavailable, polling every 1.5 s", cls: "text-warn" },
  ended: { label: "Final results", cls: "text-ink-2" },
};

export default function SimulationRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: run, error, transport, updatedAt, mutate } = useLiveSimulation(id);
  const [cancelling, setCancelling] = useState(false);
  const [cancelErr, setCancelErr] = useState<string | null>(null);

  if (error instanceof ApiError && error.status === 404) return <ErrorNote>This simulation does not exist in your workspace.</ErrorNote>;
  if (error && !run) return <ErrorNote>{errorText(error)}</ErrorNote>;
  if (!run) return <p className="text-[13px] text-ink-3">Loading simulation…</p>;
  const s = run.summary;
  const t = TRANSPORT[transport];

  return (
    <div className="flex flex-col gap-6">
      <nav className="text-[12px] text-ink-3">
        <Link href="/app/simulations" className="hover:text-ink">
          Simulations
        </Link>{" "}
        / <Mono>{shortId(run.id)}</Mono>
      </nav>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[18px] font-semibold">{run.name}</h1>
            <RunStatus run={run} />
          </div>
          <p className="text-[12px] text-ink-3" aria-live="polite">
            <span className={t.cls}>{t.label}</span>
            {updatedAt ? <> · updated <LastUpdated at={updatedAt} /></> : null}
            {" · "}started {fmtDateTime(run.started_at)}
            {run.completed_at ? ` · ended ${fmtDateTime(run.completed_at)}` : ""}
          </p>
          {run.end_reason ? <p className="text-[12px] text-ink-2">{run.end_reason}</p> : null}
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="flex gap-2">
            {run.status === "running" ? (
              <Button
                variant="danger"
                busy={cancelling}
                onClick={async () => {
                  setCancelling(true);
                  setCancelErr(null);
                  try {
                    await mutate(await post(`/simulations/${run.id}/cancel`), { revalidate: false });
                  } catch (e) {
                    setCancelErr(errorText(e));
                  } finally {
                    setCancelling(false);
                  }
                }}
              >
                Cancel run
              </Button>
            ) : null}
            <Link href={`/app/simulations/new?from=${run.id}`} className="inline-flex h-8 items-center rounded-sm border border-rule-strong bg-panel px-3 text-[13px] font-medium hover:border-ink-3">
              Rerun with changes
            </Link>
          </div>
          <ErrorNote>{cancelErr}</ErrorNote>
        </div>
      </header>

      {s ? (
        <section className="flex flex-col gap-3" aria-label="Progress">
          <div className="flex items-baseline justify-between text-[12px]">
            <span className="text-ink-2">
              <span className="font-mono text-ink">{s.finished}</span> of <span className="font-mono">{s.deliveries}</span> deliveries
              finished · {s.events} events
            </span>
            <span className="font-mono text-ink-2">{fmtMs(s.elapsed_ms)}</span>
          </div>
          <div className="h-1.5 rounded-full bg-rule" role="progressbar" aria-valuenow={Math.round(s.progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Run progress">
            <div className="h-1.5 rounded-full bg-accent transition-[width]" style={{ width: `${s.progress * 100}%` }} />
          </div>
        </section>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section className="flex flex-col gap-3">
          <SectionHeader title="Results" />
          {s ? <SummaryPanel s={s} /> : <p className="text-[13px] text-ink-3">No measurements yet.</p>}
        </section>
        <section className="flex flex-col gap-3">
          <SectionHeader title="Configuration" />
          <ConfigSummary c={run.configuration} preset={run.preset} />
        </section>
      </div>

      <DrillDown runId={run.id} running={run.status === "running"} />
    </div>
  );
}

function LastUpdated({ at }: { at: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const s = Math.max(0, Math.round((now - at) / 1000));
  return <span>{s < 2 ? "just now" : `${s}s ago`}</span>;
}

const FILTERS = [
  { value: "", label: "All" },
  { value: "dead_lettered", label: "Dead-lettered" },
  { value: "retried", label: "Retried" },
  { value: "succeeded", label: "Succeeded" },
  { value: "cancelled", label: "Cancelled" },
];

function DrillDown({ runId, running }: { runId: string; running: boolean }) {
  const [filter, setFilter] = useState("");
  const base = `/simulations/${runId}/events?limit=50${filter ? `&delivery_status=${filter}` : ""}`;
  const { data, error, size, setSize, isLoading } = useSWRInfinite<EventList>(
    (i, prev) => (i === 0 ? base : prev?.next_cursor ? `${base}&cursor=${encodeURIComponent(prev.next_cursor)}` : null),
    fetcher,
    { refreshInterval: running ? 3000 : 0 },
  );
  const items = data?.flatMap((p) => p.items) ?? [];
  const more = data?.[data.length - 1]?.next_cursor;
  return (
    <section className="flex flex-col gap-2">
      <SectionHeader title="Events in this run">
        <div role="tablist" aria-label="Filter events" className="flex flex-wrap gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              role="tab"
              aria-selected={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={`rounded-sm px-2 py-1 text-[12px] ${filter === f.value ? "bg-panel font-semibold text-ink" : "text-ink-2 hover:text-ink"}`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </SectionHeader>
      {error ? <ErrorNote>{errorText(error)}</ErrorNote> : null}
      {isLoading ? <p className="text-[13px] text-ink-3">Loading events…</p> : null}
      {data && items.length === 0 ? <p className="py-4 text-[13px] text-ink-3">No events match this filter.</p> : null}
      {items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-[13px]">
            <thead>
              <tr className="text-left text-[12px] text-ink-3">
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Event</th>
                <th className="py-2 pr-3 font-medium">Deliveries</th>
                <th className="py-2 font-medium">Idempotency key</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id} className="border-t border-rule hover:bg-panel">
                  <td className="py-1.5 pr-3">
                    <Status value={e.status} />
                  </td>
                  <td className="py-1.5 pr-3">
                    <Link href={`/app/events/${e.id}`} className="font-mono text-[12px] hover:underline">
                      {shortId(e.id)}
                    </Link>
                  </td>
                  <td className="py-1.5 pr-3">
                    {e.deliveries.map((d) => (
                      <span key={d.id} className="mr-3 inline-flex items-center gap-1 text-[12px]">
                        <Status value={d.status} compact />
                        {d.attempt_count} attempt{d.attempt_count === 1 ? "" : "s"}
                        {d.last_error_category ? <span className="text-ink-3">({d.last_error_category})</span> : null}
                      </span>
                    ))}
                  </td>
                  <td className="py-1.5 font-mono text-[12px] text-ink-2">{e.idempotency_key ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {more ? (
        <Button className="self-start" onClick={() => setSize(size + 1)}>
          Load more
        </Button>
      ) : null}
    </section>
  );
}
