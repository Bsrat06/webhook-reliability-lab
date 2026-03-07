"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { ApiError, errorText, fetcher, post } from "@/lib/api";
import type { Attempt, Delivery, EventDetail, Transition } from "@/lib/types";
import { Button, ErrorNote, Mono, SectionHeader, Status, fmtDateTime, fmtMs, fmtTime, relTime, shortId } from "@/components/ui";
import { ReplayControl } from "@/components/replay";

const TERMINAL = ["succeeded", "dead_lettered", "cancelled"];
const RECEIVER_RESULT: Record<string, string> = {
  processed: "Processed by the receiver (first time this key was seen)",
  duplicate: "Duplicate suppressed: the receiver had already processed this idempotency key",
  rejected: "Rejected by the receiver before processing",
  timed_out: "Receiver did not answer in time",
};

export default function EventInspector({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, mutate } = useSWR<EventDetail>(`/events/${id}`, fetcher, {
    refreshInterval: (d) => (!d || d.status === "in_progress" ? 1000 : 0),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const attempt = data?.attempts.find((a) => a.id === selected) ?? null;

  // Auto-select the latest attempt so the inspector is never empty once something happened.
  useEffect(() => {
    if (!selected && data?.attempts.length) setSelected(data.attempts[data.attempts.length - 1].id);
  }, [data, selected]);

  if (error instanceof ApiError && error.status === 404)
    return <ErrorNote>This event does not exist in your workspace.</ErrorNote>;
  if (error) return <ErrorNote>Could not load this event. Retrying…</ErrorNote>;
  if (!data) return <p className="text-[13px] text-ink-3">Loading event…</p>;

  return (
    <div className="flex flex-col gap-5">
      <nav className="text-[12px] text-ink-3">
        <Link href="/app/events" className="hover:text-ink">
          Events
        </Link>{" "}
        / <Mono>{shortId(data.id)}</Mono>
      </nav>
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-[18px] font-semibold">{data.event_type}</h1>
          <Status value={data.status} />
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12px] sm:grid-cols-[auto_1fr_auto_1fr]">
          <dt className="text-ink-3">Event ID</dt>
          <dd><Mono>{data.id}</Mono></dd>
          <dt className="text-ink-3">Correlation</dt>
          <dd><Mono>{data.correlation_id}</Mono></dd>
          <dt className="text-ink-3">Created</dt>
          <dd>{fmtDateTime(data.created_at)}</dd>
          <dt className="text-ink-3">Idempotency key</dt>
          <dd>{data.idempotency_key ? <Mono>{data.idempotency_key}</Mono> : <span className="text-ink-3">none</span>}</dd>
        </dl>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <section className="flex flex-col gap-4" aria-label="Deliveries">
          {data.deliveries.map((d) => (
            <DeliveryTimeline
              key={d.id}
              delivery={d}
              attempts={data.attempts.filter((a) => a.delivery_id === d.id)}
              transitions={data.transitions.filter((t) => t.delivery_id === d.id)}
              selected={selected}
              onSelect={setSelected}
              onChange={() => mutate()}
            />
          ))}
          <details className="rounded-md border border-rule bg-panel">
            <summary className="cursor-pointer px-3 py-2 text-[13px] font-medium">Payload</summary>
            <pre className="overflow-x-auto border-t border-rule bg-code p-3 font-mono text-[12px]">
              {JSON.stringify(data.payload, null, 2)}
            </pre>
          </details>
        </section>
        <section aria-label="Attempt inspector" className="lg:sticky lg:top-4 lg:self-start">
          {attempt ? (
            <AttemptInspector attempt={attempt} />
          ) : (
            <div className="rounded-md border border-dashed border-rule-strong p-6 text-[13px] text-ink-3">
              No attempts yet. The worker picks up queued deliveries within about a second. If this stays empty,
              check the worker status in the sidebar.
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

type Row = { kind: "transition"; at: string; t: Transition } | { kind: "attempt"; at: string; a: Attempt };

function DeliveryTimeline({
  delivery: d,
  attempts,
  transitions,
  selected,
  onSelect,
  onChange,
}: {
  delivery: Delivery;
  attempts: Attempt[];
  transitions: Transition[];
  selected: string | null;
  onSelect: (id: string) => void;
  onChange: () => void;
}) {
  const rows = useMemo<Row[]>(() => {
    const r: Row[] = [
      ...transitions.map((t) => ({ kind: "transition" as const, at: t.created_at, t })),
      ...attempts.map((a) => ({ kind: "attempt" as const, at: a.request_completed_at, a })),
    ];
    // Attempts sort just before the transition they caused.
    return r.sort((x, y) => x.at.localeCompare(y.at) || (x.kind === "attempt" ? -1 : 1));
  }, [attempts, transitions]);
  const p = d.retry_policy;
  const [cancelErr, setCancelErr] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  return (
    <article id={`delivery-${d.id}`} className="rounded-md border border-rule bg-panel">
      <header className="flex flex-col gap-1 border-b border-rule px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-ink-3">→</span>
            <span className="font-medium">{d.endpoint_name}</span>
            <Status value={d.status} />
            <Mono className="text-ink-3">{shortId(d.id)}</Mono>
          </div>
          <span className="text-[12px] text-ink-3">
            {d.attempt_count}/{p.max_attempts} attempts · backoff ×{p.multiplier} from {fmtMs(p.initial_delay_ms)}, cap{" "}
            {fmtMs(p.max_delay_ms)}, {p.jitter} jitter, {fmtMs(p.timeout_ms ?? 5000)} timeout
          </span>
        </div>
        {d.replay_of_delivery_id ? (
          <a href={`#delivery-${d.replay_of_delivery_id}`} className="text-[12px] text-ink-2 hover:text-ink">
            ↺ Replay of delivery <Mono>{shortId(d.replay_of_delivery_id)}</Mono>
          </a>
        ) : null}
      </header>
      <ol className="flex flex-col px-3 py-2">
        {rows.map((r, i) =>
          r.kind === "transition" ? (
            <li key={`t${i}`} className="grid grid-cols-[64px_1fr] gap-2 py-1 text-[12px] text-ink-2">
              <Mono className="text-ink-3">{fmtTime(r.at)}</Mono>
              <span>
                <Status value={r.t.to_status} />
                {r.t.reason ? <span className="ml-2 text-ink-3">{r.t.reason}</span> : null}
              </span>
            </li>
          ) : (
            <li key={r.a.id} className="py-0.5">
              <button
                onClick={() => onSelect(r.a.id)}
                aria-pressed={selected === r.a.id}
                className={`grid w-full grid-cols-[64px_1fr_auto] items-center gap-2 rounded-sm border px-0 py-1.5 text-left text-[13px] ${
                  selected === r.a.id ? "border-accent bg-paper" : "border-transparent hover:border-rule-strong"
                }`}
              >
                <Mono className="pl-1 text-ink-3">#{r.a.attempt_number}</Mono>
                <span className="flex items-center gap-2">
                  <Status value={r.a.outcome} compact />
                  <span className="font-medium">
                    {r.a.response_status ? `HTTP ${r.a.response_status}` : r.a.error_category}
                  </span>
                </span>
                <Mono className="pr-2 text-ink-2">{fmtMs(r.a.duration_ms)}</Mono>
              </button>
            </li>
          ),
        )}
        {d.status === "retry_scheduled" && d.next_attempt_at ? <NextAttempt at={d.next_attempt_at} /> : null}
        {d.dead_letter_reason ? (
          <li className="mt-1 border-l-2 border-bad pl-2 text-[12px] text-bad">Dead-lettered: {d.dead_letter_reason}</li>
        ) : null}
      </ol>
      <footer className="flex flex-wrap items-start justify-end gap-2 border-t border-rule px-3 py-2">
        {TERMINAL.includes(d.status) ? (
          <ReplayControl path={`/deliveries/${d.id}/replay`} onDone={onChange} />
        ) : (
          <div className="flex flex-col items-end gap-1">
            <Button
              variant="danger"
              busy={cancelling}
              onClick={async () => {
                setCancelling(true);
                setCancelErr(null);
                try {
                  await post(`/deliveries/${d.id}/cancel`);
                  onChange();
                } catch (e) {
                  setCancelErr(errorText(e));
                } finally {
                  setCancelling(false);
                }
              }}
            >
              Cancel delivery
            </Button>
            <ErrorNote>{cancelErr}</ErrorNote>
          </div>
        )}
      </footer>
    </article>
  );
}

function NextAttempt({ at }: { at: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, []);
  const due = new Date(at).getTime() <= now;
  return (
    <li className="py-1 pl-[72px] text-[12px] text-warn">
      {due ? "Next attempt is due; waiting for a worker…" : `Next attempt ${relTime(at, now)}`}
    </li>
  );
}

function KV({ data }: { data: Record<string, string> }) {
  const entries = Object.entries(data);
  if (!entries.length) return <p className="text-[12px] text-ink-3">none</p>;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-[12px]">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k}</dt>
          <dd className="break-all">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function AttemptInspector({ attempt: a }: { attempt: Attempt }) {
  return (
    <div className="flex flex-col gap-4 rounded-md border border-rule bg-panel p-4">
      <SectionHeader title={`Attempt #${a.attempt_number}`}>
        <Status value={a.outcome} />
      </SectionHeader>
      <dl className="grid grid-cols-2 gap-3 text-[12px] sm:grid-cols-4">
        {[
          ["Status", a.response_status ?? "—"],
          ["Duration", fmtMs(a.duration_ms)],
          ["Started", fmtTime(a.request_started_at)],
          ["Error", a.error_category ?? "—"],
        ].map(([k, v]) => (
          <div key={k as string}>
            <dt className="text-ink-3">{k}</dt>
            <dd className="font-mono text-[13px]">{v}</dd>
          </div>
        ))}
      </dl>
      {a.error_message ? <p className="text-[12px] text-ink-2">{a.error_message}</p> : null}
      {a.response_headers["x-receiver-result"] ? (
        <p className="border-l-2 border-rule-strong pl-2 text-[12px] text-ink-2">
          {RECEIVER_RESULT[a.response_headers["x-receiver-result"]] ?? a.response_headers["x-receiver-result"]}
        </p>
      ) : null}
      <div className="flex flex-col gap-1">
        <h3 className="text-[12px] font-medium text-ink-2">Request headers</h3>
        <KV data={a.request_headers} />
      </div>
      <div className="flex flex-col gap-1">
        <h3 className="text-[12px] font-medium text-ink-2">Response headers</h3>
        <KV data={a.response_headers} />
      </div>
      <div className="flex flex-col gap-1">
        <h3 className="text-[12px] font-medium text-ink-2">Response body (first 2 KB)</h3>
        <pre className="overflow-x-auto rounded-sm bg-code p-2 font-mono text-[12px] whitespace-pre-wrap">
          {a.response_body_excerpt || <span className="text-ink-3">empty</span>}
        </pre>
      </div>
      <p className="text-[12px] text-ink-3">
        Worker <Mono>{a.worker_id}</Mono>
      </p>
    </div>
  );
}
