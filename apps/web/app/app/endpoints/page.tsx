"use client";

import { useState } from "react";
import useSWR from "swr";
import { del, errorText, fetcher, patch, post } from "@/lib/api";
import type { Endpoint, IdempotencyRecord, ReceiverConfig, RetryPolicy } from "@/lib/types";
import { ReceiverFields, RetryPolicyFields } from "@/components/config-fields";
import { Button, ErrorNote, Mono, Status, fmtDateTime, inputCls } from "@/components/ui";


export default function EndpointsPage() {
  const { data, error, mutate } = useSWR<Endpoint[]>("/endpoints", fetcher);
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  async function create() {
    setCreating(true);
    setCreateErr(null);
    try {
      const r = await post<{ endpoint: Endpoint; signing_secret: string }>("/endpoints", {
        name: `Receiver ${(data?.length ?? 0) + 1}`,
        endpoint_type: "builtin",
      });
      setSecret({ name: r.endpoint.name, value: r.signing_secret });
      mutate();
    } catch (e) {
      setCreateErr(errorText(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[18px] font-semibold">Endpoints</h1>
        <Button variant="primary" onClick={create} busy={creating}>
          New built-in receiver
        </Button>
      </div>
      <p className="max-w-prose text-[13px] text-ink-2">
        Built-in receivers are real HTTP endpoints hosted by the platform. Every delivery is signed with HMAC-SHA256 and
        verified by the receiver, which then responds according to its behavior below. External HTTPS destinations
        are disabled on this deployment until SSRF protections are in place. Delivery is at-least-once: retries and
        lost acknowledgements can deliver the same event more than once, so receivers should be idempotent.
      </p>
      <ErrorNote>{createErr}</ErrorNote>
      {secret ? (
        <div role="status" className="flex flex-col gap-1 border-l-2 border-accent bg-panel px-3 py-2 text-[13px]">
          <span>
            Signing secret for <strong>{secret.name}</strong>. It is shown only once:
          </span>
          <Mono className="break-all select-all">{secret.value}</Mono>
          <Button variant="ghost" className="self-start" onClick={() => setSecret(null)}>
            Dismiss
          </Button>
        </div>
      ) : null}
      {error ? <ErrorNote>{errorText(error)}</ErrorNote> : null}
      {!data && !error ? <p className="text-[13px] text-ink-3">Loading endpoints…</p> : null}
      {data?.length === 0 ? <p className="text-[13px] text-ink-2">No endpoints. Create a built-in receiver to start.</p> : null}
      {data?.map((ep) => (
        <EndpointCard key={ep.id} ep={ep} onChange={() => mutate()} onSecret={(v) => setSecret({ name: ep.name, value: v })} />
      ))}
    </div>
  );
}

function EndpointCard({ ep, onChange, onSecret }: { ep: Endpoint; onChange: () => void; onSecret: (s: string) => void }) {
  const [rc, setRc] = useState<ReceiverConfig>(ep.receiver_config);
  const [rp, setRp] = useState<RetryPolicy>(ep.retry_policy);
  const [name, setName] = useState(ep.name);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const dirty =
    name !== ep.name || JSON.stringify(rc) !== JSON.stringify(ep.receiver_config) || JSON.stringify(rp) !== JSON.stringify(ep.retry_policy);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setErr(null);
    setSaved(false);
    try {
      await fn();
      onChange();
      if (label === "save") setSaved(true);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const total = Object.values(ep.stats).reduce((a, b) => a + (b ?? 0), 0);

  return (
    <article className="rounded-md border border-rule bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-4 py-2">
        <div className="flex flex-wrap items-center gap-3">
          <input aria-label="Endpoint name" className={`${inputCls} w-56 font-medium`} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          <span className="text-[12px] text-ink-3">{ep.enabled ? "Enabled" : "Disabled"} · built-in</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button busy={busy === "toggle"} onClick={() => run("toggle", () => patch(`/endpoints/${ep.id}`, { enabled: !ep.enabled }))}>
            {ep.enabled ? "Disable" : "Enable"}
          </Button>
          <Button busy={busy === "rotate"} onClick={() => run("rotate", async () => onSecret((await post<{ signing_secret: string }>(`/endpoints/${ep.id}/rotate-secret`)).signing_secret))}>
            Rotate secret
          </Button>
          <Button
            variant="danger"
            busy={busy === "delete"}
            onClick={() => {
              if (confirm(`Delete "${ep.name}" and all its deliveries? This cannot be undone.`)) run("delete", () => del(`/endpoints/${ep.id}`));
            }}
          >
            Delete
          </Button>
        </div>
      </header>

      <div className="grid gap-6 p-4 lg:grid-cols-2">
        <ReceiverFields id={ep.id} rc={rc} setRc={setRc} />
        <RetryPolicyFields rp={rp} setRp={setRp} />
      </div>

      <IdempotencyRecords endpointId={ep.id} enabled={ep.receiver_config.idempotency} />

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-rule px-4 py-2">
        <div className="flex flex-wrap items-center gap-3 text-[12px] text-ink-2">
          {total === 0 ? (
            <span className="text-ink-3">No deliveries yet</span>
          ) : (
            (["succeeded", "retry_scheduled", "processing", "pending", "dead_lettered", "cancelled"] as const)
              .filter((s) => ep.stats[s])
              .map((s) => (
                <span key={s} className="flex items-center gap-1">
                  <Status value={s} compact />
                  {ep.stats[s]} {s.replace("_", " ")}
                </span>
              ))
          )}
        </div>
        <div className="flex items-center gap-2">
          {saved && !dirty ? <span role="status" className="text-[12px] text-ok">Saved</span> : null}
          <ErrorNote>{err}</ErrorNote>
          <Button variant="primary" disabled={!dirty} busy={busy === "save"} onClick={() => run("save", () => patch(`/endpoints/${ep.id}`, { name, receiver_config: rc, retry_policy: rp }))}>
            Save changes
          </Button>
        </div>
      </footer>
    </article>
  );
}

function IdempotencyRecords({ endpointId, enabled }: { endpointId: string; enabled: boolean }) {
  const [open, setOpen] = useState(false);
  const { data, error } = useSWR<IdempotencyRecord[]>(open ? `/endpoints/${endpointId}/idempotency-records?limit=25` : null, fetcher, {
    refreshInterval: open ? 3000 : 0,
  });
  return (
    <details className="border-t border-rule px-4 py-2" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-[13px] font-medium">Processed idempotency keys</summary>
      <div className="flex flex-col gap-2 py-2 text-[12px]">
        <p className="text-ink-2">
          {enabled
            ? "Keys this receiver has processed. A unique database constraint on (receiver, key) guarantees each key is processed once, even when duplicates arrive concurrently. Keys expire after 24 hours."
            : "Idempotency is off for this receiver, so duplicates are processed again. Turn it on above to see suppression."}{" "}
          This only demonstrates the pattern. The platform cannot make a third-party receiver idempotent.
        </p>
        {error ? <ErrorNote>{errorText(error)}</ErrorNote> : null}
        {data && data.length === 0 ? <p className="text-ink-3">No keys recorded yet.</p> : null}
        {data && data.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-left">
              <thead className="text-ink-3">
                <tr>
                  <th className="py-1 pr-3 font-medium">Key</th>
                  <th className="py-1 pr-3 font-medium">Received</th>
                  <th className="py-1 pr-3 font-medium">Processed</th>
                  <th className="py-1 font-medium">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {data.map((r) => (
                  <tr key={r.idempotency_key} className="border-t border-rule">
                    <td className="py-1 pr-3 font-mono break-all">{r.idempotency_key}</td>
                    <td className="py-1 pr-3 font-mono">{r.seen_count}×</td>
                    <td className="py-1 pr-3">once{r.seen_count > 1 ? `, ${r.seen_count - 1} suppressed` : ""}</td>
                    <td className="py-1">{fmtDateTime(r.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </details>
  );
}
