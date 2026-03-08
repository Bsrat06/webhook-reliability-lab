"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { errorText, fetcher, post } from "@/lib/api";
import type { AuditEntry, Session } from "@/lib/types";
import { Button, ErrorNote, Mono, SectionHeader, fmtDateTime, inputCls, shortId } from "@/components/ui";

export default function WorkspacePage() {
  const { data } = useSWR<Session>("/session", fetcher);
  if (!data) return <p className="text-[13px] text-ink-3">Loading workspace…</p>;
  const usage = [
    ["Endpoints", data.usage.endpoints, data.limits.endpoints],
    ["Events in the last hour", data.usage.events_last_hour, data.limits.events_per_hour],
    ["Replays in the last hour", data.usage.replays_last_hour, data.limits.replays_per_hour],
    ["Simulated events in the last hour", data.usage.simulation_events_last_hour, data.limits.simulation_events_per_hour],
    ["Simulations running", data.usage.simulations_active, data.limits.concurrent_simulations],
  ] as const;
  return (
    <div className="flex max-w-2xl flex-col gap-6">
      <h1 className="text-[18px] font-semibold">Workspace</h1>
      <section className="flex flex-col gap-2">
        <SectionHeader title="Guest workspace" />
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
          <dt className="text-ink-3">ID</dt>
          <dd><Mono>{data.workspace.id}</Mono></dd>
          <dt className="text-ink-3">Created</dt>
          <dd>{fmtDateTime(data.workspace.created_at)}</dd>
          <dt className="text-ink-3">Deleted after</dt>
          <dd>{data.workspace.expires_at ? fmtDateTime(data.workspace.expires_at) : "never"} if inactive</dd>
        </dl>
        <p className="text-[13px] text-ink-2">
          Guest data is kept for {data.retention_hours} hours after your last activity. Each visit extends the window.
          Your session lives in an HttpOnly cookie in this browser. Clearing cookies ends access to this workspace.
        </p>
      </section>
      <section className="flex flex-col gap-2">
        <SectionHeader title="Usage and limits" />
        <table className="text-[13px]">
          <tbody>
            {usage.map(([label, used, limit]) => (
              <tr key={label} className="border-t border-rule">
                <td className="py-2 pr-4">{label}</td>
                <td className="py-2 pr-4 font-mono">
                  {used} / {limit}
                </td>
                <td className="w-1/2 py-2">
                  <div className="h-1.5 rounded-full bg-rule" role="meter" aria-valuenow={used} aria-valuemin={0} aria-valuemax={limit} aria-label={label}>
                    <div className={`h-1.5 rounded-full ${used >= limit ? "bg-bad" : "bg-ink-2"}`} style={{ width: `${Math.min(100, (used / limit) * 100)}%` }} />
                  </div>
                </td>
              </tr>
            ))}
            <tr className="border-t border-rule">
              <td className="py-2 pr-4">Deliveries in flight at once</td>
              <td className="py-2 font-mono" colSpan={2}>
                {data.limits.workspace_concurrency}
              </td>
            </tr>
            <tr className="border-t border-rule">
              <td className="py-2 pr-4">Events per simulation · max runtime</td>
              <td className="py-2 font-mono" colSpan={2}>
                {data.limits.simulation_events} · {data.limits.simulation_runtime_seconds} s
              </td>
            </tr>
            <tr className="border-t border-rule">
              <td className="py-2 pr-4">Max deliveries per bulk replay</td>
              <td className="py-2 font-mono" colSpan={2}>
                {data.limits.bulk_replay}
              </td>
            </tr>
            <tr className="border-t border-rule">
              <td className="py-2 pr-4">Max payload size</td>
              <td className="py-2 font-mono" colSpan={2}>
                {Math.round(data.limits.payload_bytes / 1024)} KB
              </td>
            </tr>
          </tbody>
        </table>
        <p className="text-[12px] text-ink-3">
          Limits are enforced by the server. Requests over a limit get a clear error and are never silently dropped.
          Deliveries beyond the in-flight limit wait in the durable queue.
        </p>
      </section>
      <ExportData />
      <AuditLog />
      <DangerZone />
    </div>
  );
}

function AuditLog() {
  const { data, error } = useSWR<AuditEntry[]>("/workspaces/current/audit?limit=50", fetcher, { refreshInterval: 10_000 });
  return (
    <section className="flex flex-col gap-2">
      <SectionHeader title="Audit log" />
      <p className="text-[12px] text-ink-3">
        Changes to endpoints, replays, cancellations and dead-letter resolutions. Secrets and payloads are never recorded here.
      </p>
      {error ? <ErrorNote>Could not load the audit log.</ErrorNote> : null}
      {data && data.length === 0 ? <p className="text-[13px] text-ink-3">No audited actions yet.</p> : null}
      {data && data.length > 0 ? (
        <ol className="flex flex-col text-[12px]">
          {data.map((a) => (
            <li key={a.id} className="grid grid-cols-[110px_1fr] gap-2 border-t border-rule py-1.5 sm:grid-cols-[130px_180px_1fr]">
              <span className="text-ink-3">{fmtDateTime(a.created_at)}</span>
              <span className="font-mono">{a.action}</span>
              <span className="col-span-2 text-ink-2 sm:col-span-1">
                {a.resource_type} {a.resource_id ? <Mono>{shortId(a.resource_id)}</Mono> : null}
                {Object.keys(a.metadata).length ? (
                  <span className="text-ink-3">
                    {" "}
                    {Object.entries(a.metadata)
                      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`)
                      .join(" ")}
                  </span>
                ) : null}
                <span className="block text-ink-3">by {a.actor}</span>
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

const DATASETS = [
  ["events", "Events", "Payloads, types, idempotency keys and correlation IDs"],
  ["deliveries", "Deliveries", "Status, attempt counts and replay lineage per endpoint"],
  ["attempts", "Attempts", "Every HTTP attempt: timing, status, error category, receiver result"],
  ["dead_letters", "Dead letters", "Failure reasons and resolutions"],
  ["simulations", "Simulations", "Configuration, seed and result summary of each run"],
  ["analytics", "Analytics summary", "All-time metrics for this workspace"],
] as const;

function ExportData() {
  return (
    <section className="flex flex-col gap-2">
      <SectionHeader title="Export data" />
      <p className="text-[12px] text-ink-3">
        Exports are limited to 10,000 rows per file; a truncated file says so in its X-Export-Truncated header and JSON
        body. Signing secrets and request headers are never exported. CSV cells that start with a formula character
        are prefixed with an apostrophe.
      </p>
      <table className="text-[13px]">
        <tbody>
          {DATASETS.map(([key, label, hint]) => (
            <tr key={key} className="border-t border-rule">
              <td className="py-2 pr-4">
                {label}
                <span className="block text-[12px] text-ink-3">{hint}</span>
              </td>
              <td className="py-2 text-right whitespace-nowrap">
                <a className="mr-3 text-accent hover:underline" href={`/api/v1/exports/${key}?format=csv`} download>
                  CSV<span className="sr-only"> export of {label}</span>
                </a>
                <a className="text-accent hover:underline" href={`/api/v1/exports/${key}?format=json`} download>
                  JSON<span className="sr-only"> export of {label}</span>
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function DangerZone() {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function act(kind: "reset" | "delete") {
    setBusy(kind);
    setErr(null);
    try {
      await post(`/workspaces/current/${kind}`, { confirm: kind });
      if (kind === "delete") {
        router.replace("/?deleted=1");
        return;
      }
      setTyped("");
      setDone(true);
      mutate(() => true);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }
  return (
    <section className="flex flex-col gap-3 rounded-md border border-bad/40 p-4">
      <h2 className="text-[13px] font-semibold text-bad">Reset or delete</h2>
      <p className="text-[13px] text-ink-2">
        <strong>Reset</strong> permanently deletes every event, delivery, attempt, dead letter, simulation and
        idempotency key, and replaces your endpoints with one default receiver. Your session and audit log are kept.
        <strong> Delete</strong> removes the whole workspace now instead of waiting for the retention window.
      </p>
      <label className="flex flex-col gap-1 text-[12px] text-ink-2">
        Type <Mono>reset</Mono> or <Mono>delete</Mono> to confirm
        <input className={`${inputCls} w-48 font-mono`} value={typed} onChange={(e) => setTyped(e.target.value.trim())} autoComplete="off" />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button variant="danger" disabled={typed !== "reset"} busy={busy === "reset"} onClick={() => act("reset")}>
          Reset workspace
        </Button>
        <Button variant="danger" disabled={typed !== "delete"} busy={busy === "delete"} onClick={() => act("delete")}>
          Delete workspace now
        </Button>
      </div>
      {done ? <p role="status" className="text-[12px] text-ok">Workspace reset. You can start fresh.</p> : null}
      <ErrorNote>{err}</ErrorNote>
    </section>
  );
}
