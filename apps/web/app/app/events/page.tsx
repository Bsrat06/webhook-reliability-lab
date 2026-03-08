"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { Endpoint, EventList } from "@/lib/types";
import { Button, ErrorNote, Mono, SectionHeader, Status, fmtDateTime, inputCls, shortId } from "@/components/ui";
import { SendEventForm } from "@/components/send-event-form";

export default function EventsPage() {
  const [q, setQ] = useState("");
  const [source, setSource] = useState<"manual" | "simulation" | "">("manual");
  const [composing, setComposing] = useState(false);
  const key = `/events?limit=50${source ? `&source=${source}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`;
  const { data, error, isLoading, mutate } = useSWR<EventList>(key, fetcher, {
    // Poll only while something is still moving; the database is the source of truth.
    refreshInterval: (d) => (d?.items.some((e) => e.status === "in_progress") ? 1000 : 0),
  });
  const { data: endpoints } = useSWR<Endpoint[]>("/endpoints", fetcher);
  const epName = (id: string) => endpoints?.find((e) => e.id === id)?.name ?? shortId(id);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[18px] font-semibold">Events</h1>
        <Button variant="primary" onClick={() => setComposing((v) => !v)} aria-expanded={composing}>
          {composing ? "Close" : "Send event"}
        </Button>
      </div>

      {composing && endpoints ? (
        <SendEventForm
          endpoints={endpoints}
          onSent={() => {
            mutate();
          }}
        />
      ) : null}

      <section className="flex flex-col gap-2">
        <SectionHeader title="Recent events">
          <div role="tablist" aria-label="Event source" className="flex gap-1">
            {([["manual", "Sent manually"], ["simulation", "From simulations"], ["", "All"]] as const).map(([v, label]) => (
              <button
                key={v}
                role="tab"
                aria-selected={source === v}
                onClick={() => setSource(v)}
                className={`rounded-sm px-2 py-1 text-[12px] ${source === v ? "bg-paper font-semibold text-ink" : "text-ink-2 hover:text-ink"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <input
            aria-label="Search by type, correlation ID or idempotency key"
            placeholder="Search type, correlation, idempotency key"
            className={`${inputCls} w-64`}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </SectionHeader>
        {error ? <ErrorNote>Could not load events. Retrying automatically.</ErrorNote> : null}
        {isLoading ? <p className="py-6 text-[13px] text-ink-3">Loading events…</p> : null}
        {data && data.items.length === 0 ? (
          <div className="flex flex-col items-start gap-2 py-8 text-[13px] text-ink-2">
            <p>
              {q
                ? "No events match that search."
                : source === "simulation"
                  ? "No simulation events yet. Run a simulation from the Simulation lab."
                  : "No events yet. Send one to the built-in receiver to see a real delivery."}
            </p>
            {!q && !composing && source !== "simulation" ? (
              <Button variant="primary" onClick={() => setComposing(true)}>
                Send your first event
              </Button>
            ) : null}
          </div>
        ) : null}
        {data && data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-[13px]">
              <thead>
                <tr className="text-left text-[12px] text-ink-3">
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">Type</th>
                  <th className="py-2 pr-3 font-medium">Deliveries</th>
                  <th className="py-2 pr-3 font-medium">Event</th>
                  <th className="py-2 font-medium">Created</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id} className="border-t border-rule hover:bg-panel">
                    <td className="py-2 pr-3">
                      <Status value={e.status} />
                    </td>
                    <td className="py-2 pr-3">
                      <Link href={`/app/events/${e.id}`} className="font-medium hover:underline">
                        {e.event_type}
                      </Link>
                    </td>
                    <td className="py-2 pr-3">
                      <ul className="flex flex-col gap-0.5">
                        {e.deliveries.map((d) => (
                          <li key={d.id} className="flex items-center gap-2 text-[12px]">
                            <Status value={d.status} compact />
                            <span className="text-ink-2">{epName(d.endpoint_id)}</span>
                            <span className="text-ink-3">
                              {d.attempt_count} attempt{d.attempt_count === 1 ? "" : "s"}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </td>
                    <td className="py-2 pr-3">
                      <Mono className="text-ink-2">{shortId(e.id)}</Mono>
                    </td>
                    <td className="py-2 text-ink-2">{fmtDateTime(e.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
