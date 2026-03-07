"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import { errorText, fetcher, post } from "@/lib/api";
import type { Attempt, DeadLetter, DeadLetterList, ReplayPolicy, Session } from "@/lib/types";
import { PolicySelect, ReplayControl } from "@/components/replay";
import { Button, ErrorNote, Field, Mono, SectionHeader, Status, fmtDateTime, fmtMs, inputCls, shortId } from "@/components/ui";

type State = "open" | "resolved" | "all";
type Detail = DeadLetter & { payload: unknown; retry_policy: Record<string, unknown>; attempts: Attempt[] };

export default function DeadLettersPage() {
  const [state, setState] = useState<State>("open");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openId, setOpenId] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);

  // Debounce search so typing doesn't fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const base = `/dead-letters?state=${state}&limit=50${query ? `&q=${encodeURIComponent(query)}` : ""}`;
  const { data, error, size, setSize, mutate, isLoading } = useSWRInfinite<DeadLetterList>(
    (i, prev) => (i === 0 ? base : prev?.next_before ? `${base}&before=${encodeURIComponent(prev.next_before)}` : null),
    fetcher,
    // Replays in flight change rows; poll gently while any replay is still moving.
    { refreshInterval: (d) => (d?.some((p) => p.items.some((i) => i.replays.some((r) => !["succeeded", "dead_lettered", "cancelled"].includes(r.status)))) ? 2000 : 0) },
  );
  const { data: session, mutate: mutateSession } = useSWR<Session>("/session", fetcher);
  const items = data?.flatMap((p) => p.items) ?? [];
  const counts = data?.[0]?.counts;
  const more = data?.[data.length - 1]?.next_before;
  const bulkLimit = session?.limits.bulk_replay ?? 50;

  function refresh() {
    mutate();
    mutateSession();
  }

  function toggle(id: string, on: boolean) {
    setSelected((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
  }

  const selectedItems = items.filter((i) => selected.has(i.id));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[18px] font-semibold">Dead-letter queue</h1>
        <Button variant="primary" disabled={selected.size === 0} onClick={() => setBulkOpen(true)}>
          Replay selected ({selected.size})
        </Button>
      </div>
      <p className="max-w-prose text-[13px] text-ink-2">
        Deliveries land here when retries run out or the response is not retryable. Nothing is discarded: the event and
        every attempt stay intact. Replaying creates a new, linked delivery and marks this record resolved. Dismissing
        resolves it without sending anything. Both actions are recorded in the audit log.
      </p>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
        <section className="flex min-w-0 flex-col gap-2">
          <SectionHeader title="Records">
            <div role="tablist" aria-label="Filter by state" className="flex gap-1">
              {(["open", "resolved", "all"] as const).map((s) => (
                <button
                  key={s}
                  role="tab"
                  aria-selected={state === s}
                  onClick={() => {
                    setState(s);
                    setSelected(new Set());
                  }}
                  className={`rounded-sm px-2 py-1 text-[12px] ${state === s ? "bg-paper font-semibold text-ink" : "text-ink-2 hover:text-ink"}`}
                >
                  {s === "open" ? `Open${counts ? ` ${counts.open}` : ""}` : s === "resolved" ? `Resolved${counts ? ` ${counts.resolved}` : ""}` : "All"}
                </button>
              ))}
            </div>
            <input aria-label="Search event type or failure reason" placeholder="Search type or reason" className={`${inputCls} w-56`} value={q} onChange={(e) => setQ(e.target.value)} />
          </SectionHeader>
          {error ? <ErrorNote>{errorText(error)}</ErrorNote> : null}
          {isLoading ? <p className="py-6 text-[13px] text-ink-3">Loading dead letters…</p> : null}
          {data && items.length === 0 ? (
            <p className="py-8 text-[13px] text-ink-2">
              {query
                ? "No records match that search."
                : state === "open"
                  ? "Nothing in the dead-letter queue. Set an endpoint to “Always fail” and send an event to see one arrive."
                  : "No records here yet."}
            </p>
          ) : null}
          {items.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse text-[13px]">
                <thead>
                  <tr className="text-left text-[12px] text-ink-3">
                    <th className="w-8 py-2 pr-2 font-medium">
                      <input
                        type="checkbox"
                        aria-label="Select all visible records"
                        checked={items.length > 0 && items.slice(0, bulkLimit).every((i) => selected.has(i.id))}
                        onChange={(e) => setSelected(e.target.checked ? new Set(items.slice(0, bulkLimit).map((i) => i.id)) : new Set())}
                      />
                    </th>
                    <th className="py-2 pr-3 font-medium">Event</th>
                    <th className="py-2 pr-3 font-medium">Endpoint</th>
                    <th className="py-2 pr-3 font-medium">Reason</th>
                    <th className="py-2 pr-3 font-medium">Replays</th>
                    <th className="py-2 font-medium">Dead-lettered</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id} className={`border-t border-rule ${openId === i.id ? "bg-panel" : "hover:bg-panel"}`}>
                      <td className="py-2 pr-2 align-top">
                        <input type="checkbox" aria-label={`Select ${i.event_type} ${shortId(i.id)}`} checked={selected.has(i.id)} onChange={(e) => toggle(i.id, e.target.checked)} />
                      </td>
                      <td className="py-2 pr-3 align-top">
                        <button className="text-left font-medium hover:underline" onClick={() => setOpenId(i.id)} aria-expanded={openId === i.id}>
                          {i.event_type}
                        </button>
                        <div className="text-[12px] text-ink-3">
                          {i.resolved_at ? "Resolved" : "Open"} · <Mono>{shortId(i.delivery_id)}</Mono>
                        </div>
                      </td>
                      <td className="py-2 pr-3 align-top text-ink-2">{i.endpoint_name}</td>
                      <td className="py-2 pr-3 align-top">
                        <span className="font-mono text-[12px]">{i.last_status ?? i.last_error_category ?? "—"}</span>
                        <div className="text-[12px] text-ink-3">{i.reason}</div>
                      </td>
                      <td className="py-2 pr-3 align-top">
                        {i.replays.length === 0 ? (
                          <span className="text-[12px] text-ink-3">none</span>
                        ) : (
                          <ul className="flex flex-col gap-0.5">
                            {i.replays.map((r) => (
                              <li key={r.id} className="flex items-center gap-1 text-[12px]">
                                <Status value={r.status} compact />
                                <Mono>{shortId(r.id)}</Mono>
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td className="py-2 align-top text-ink-2">{fmtDateTime(i.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {more ? (
            <Button className="self-start" onClick={() => setSize(size + 1)}>
              Load older
            </Button>
          ) : null}
        </section>

        <aside aria-label="Dead-letter detail" className="lg:sticky lg:top-4 lg:self-start">
          {openId ? (
            <DeadLetterPanel id={openId} onChange={refresh} onClose={() => setOpenId(null)} />
          ) : (
            <div className="rounded-md border border-dashed border-rule-strong p-6 text-[13px] text-ink-3">
              Select a record to inspect its attempts and payload, replay it, or dismiss it.
            </div>
          )}
        </aside>
      </div>

      {bulkOpen ? (
        <BulkReplayDialog
          items={selectedItems}
          limit={bulkLimit}
          onClose={() => setBulkOpen(false)}
          onDone={() => {
            setSelected(new Set());
            setBulkOpen(false);
            refresh();
          }}
        />
      ) : null}
    </div>
  );
}

function DeadLetterPanel({ id, onChange, onClose }: { id: string; onChange: () => void; onClose: () => void }) {
  const { data, error, mutate } = useSWR<Detail>(`/dead-letters/${id}`, fetcher);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (error) return <ErrorNote>{errorText(error)}</ErrorNote>;
  if (!data) return <p className="text-[13px] text-ink-3">Loading record…</p>;
  const changed = () => {
    mutate();
    onChange();
  };
  return (
    <div className="flex flex-col gap-4 rounded-md border border-rule bg-panel p-4">
      <SectionHeader title={data.event_type}>
        <Button variant="ghost" onClick={onClose} aria-label="Close detail">
          Close
        </Button>
      </SectionHeader>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
        <dt className="text-ink-3">Reason</dt>
        <dd>{data.reason}</dd>
        <dt className="text-ink-3">Endpoint</dt>
        <dd>{data.endpoint_name}</dd>
        <dt className="text-ink-3">Delivery</dt>
        <dd>
          <Mono>{data.delivery_id}</Mono>
        </dd>
        <dt className="text-ink-3">State</dt>
        <dd>{data.resolved_at ? `Resolved ${fmtDateTime(data.resolved_at)}: ${data.resolution_note}` : "Open"}</dd>
      </dl>
      <div className="flex flex-col gap-1">
        <h3 className="text-[12px] font-medium text-ink-2">Attempts</h3>
        <ol className="flex flex-col gap-0.5 font-mono text-[12px]">
          {data.attempts.map((a) => (
            <li key={a.id} className="grid grid-cols-[32px_1fr_auto] gap-2">
              <span className="text-ink-3">#{a.attempt_number}</span>
              <span className="flex items-center gap-1">
                <Status value={a.outcome} compact />
                {a.response_status ? `HTTP ${a.response_status}` : a.error_category}
              </span>
              <span className="text-ink-2">{fmtMs(a.duration_ms)}</span>
            </li>
          ))}
        </ol>
      </div>
      <details>
        <summary className="cursor-pointer text-[12px] font-medium text-ink-2">Payload</summary>
        <pre className="mt-1 max-h-48 overflow-auto rounded-sm bg-code p-2 font-mono text-[12px]">{JSON.stringify(data.payload, null, 2)}</pre>
      </details>
      <Link href={`/app/events/${data.event_id}`} className="text-[13px] text-accent hover:underline">
        Open full event timeline →
      </Link>
      <div className="flex flex-col gap-2 border-t border-rule pt-3">
        <ReplayControl path={`/dead-letters/${data.id}/replay`} onDone={changed} label="Replay delivery" />
        {!data.resolved_at ? (
          <form
            className="flex flex-col gap-2"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setErr(null);
              try {
                await post(`/dead-letters/${data.id}/resolve`, { note });
                setNote("");
                changed();
              } catch (x) {
                setErr(errorText(x));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Resolution note (optional)" htmlFor="resolve-note">
              <input id="resolve-note" className={inputCls} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="e.g. handled manually" />
            </Field>
            <Button type="submit" busy={busy} className="self-end">
              Dismiss without replay
            </Button>
            <ErrorNote>{err}</ErrorNote>
          </form>
        ) : null}
      </div>
    </div>
  );
}

function BulkReplayDialog({ items, limit, onClose, onDone }: { items: DeadLetter[]; limit: number; onClose: () => void; onDone: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [policy, setPolicy] = useState<ReplayPolicy>("original");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  const over = items.length > limit;
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby="bulk-title"
      className="m-auto w-[min(440px,calc(100vw-32px))] rounded-md border border-rule-strong bg-panel p-0 text-ink backdrop:bg-black/40"
    >
      <div className="flex flex-col gap-3 p-4">
        <h2 id="bulk-title" className="text-[15px] font-semibold">
          Replay {items.length} deliver{items.length === 1 ? "y" : "ies"}?
        </h2>
        <p className="text-[13px] text-ink-2">
          Each creates a new delivery to the same endpoint. Receivers that are not idempotent may process these events
          twice. Replays count toward your hourly replay quota.
        </p>
        <Field label="Retry policy" htmlFor="bulk-policy">
          <PolicySelect id="bulk-policy" value={policy} onChange={setPolicy} />
        </Field>
        {over ? <ErrorNote>At most {limit} deliveries can be replayed at once. Deselect some records.</ErrorNote> : null}
        <ErrorNote>{err}</ErrorNote>
        <div className="flex justify-end gap-2">
          <Button onClick={() => ref.current?.close()}>Cancel</Button>
          <Button
            variant="primary"
            busy={busy}
            disabled={over || items.length === 0}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await post("/deliveries/replay", {
                  delivery_ids: items.map((i) => i.delivery_id),
                  confirm_count: items.length,
                  policy,
                });
                onDone();
              } catch (e) {
                setErr(errorText(e));
                setBusy(false);
              }
            }}
          >
            Replay {items.length}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
