"use client";

import { useState } from "react";
import useSWR from "swr";
import { del, errorText, fetcher, patch, post } from "@/lib/api";
import type { Endpoint, ReceiverConfig, RetryPolicy } from "@/lib/types";
import { Button, ErrorNote, Field, Mono, SectionHeader, Status, fmtMs, inputCls } from "@/components/ui";

const POLICY_PRESETS: Record<string, Partial<RetryPolicy>> = {
  Fast: { max_attempts: 4, initial_delay_ms: 500, multiplier: 2, max_delay_ms: 4000, jitter: "full" },
  Balanced: { max_attempts: 6, initial_delay_ms: 2000, multiplier: 2, max_delay_ms: 60000, jitter: "full" },
  Conservative: { max_attempts: 8, initial_delay_ms: 10000, multiplier: 3, max_delay_ms: 3600000, jitter: "equal" },
};

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
        are disabled on this deployment until SSRF protections are in place.
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
  const num = (v: string) => (v === "" ? 0 : Number(v));

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
        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 text-[13px] font-semibold">Receiver behavior</legend>
          <Field label="Responds with">
            <select className={inputCls} value={rc.mode} onChange={(e) => setRc({ ...rc, mode: e.target.value as ReceiverConfig["mode"] })}>
              <option value="succeed">Always 200 OK</option>
              <option value="fail">Always fail</option>
              <option value="fail_first_n">Fail the first N attempts, then 200</option>
              <option value="random">Fail randomly (seeded)</option>
            </select>
          </Field>
          {rc.mode !== "succeed" ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Failure status">
                <select className={inputCls} value={rc.failure_status} onChange={(e) => setRc({ ...rc, failure_status: Number(e.target.value) })}>
                  {[500, 502, 503, 504, 429, 408, 400, 401, 404, 410, 422].map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </Field>
              {rc.mode === "fail_first_n" ? (
                <Field label="Failing attempts (N)">
                  <input type="number" min={0} max={15} className={inputCls} value={rc.fail_first_n} onChange={(e) => setRc({ ...rc, fail_first_n: num(e.target.value) })} />
                </Field>
              ) : null}
              {rc.mode === "random" ? (
                <>
                  <Field label="Failure rate (%)">
                    <input type="number" min={0} max={100} className={inputCls} value={Math.round(rc.failure_rate * 100)} onChange={(e) => setRc({ ...rc, failure_rate: num(e.target.value) / 100 })} />
                  </Field>
                  <Field label="Seed" hint="Same seed, same event and attempt give the same decision.">
                    <input type="number" min={0} className={inputCls} value={rc.seed} onChange={(e) => setRc({ ...rc, seed: num(e.target.value) })} />
                  </Field>
                </>
              ) : null}
            </div>
          ) : null}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Latency min (ms)">
              <input type="number" min={0} max={15000} className={inputCls} value={rc.latency_ms_min} onChange={(e) => setRc({ ...rc, latency_ms_min: num(e.target.value) })} />
            </Field>
            <Field label="Latency max (ms)">
              <input type="number" min={0} max={15000} className={inputCls} value={rc.latency_ms_max} onChange={(e) => setRc({ ...rc, latency_ms_max: num(e.target.value) })} />
            </Field>
          </div>
          <p className="text-[12px] text-ink-3">
            Deliveries time out after 10 s. A latency above that produces a real timeout.
          </p>
        </fieldset>

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 flex items-center gap-2 text-[13px] font-semibold">
            Retry policy
            <span className="flex gap-1 font-normal">
              {Object.entries(POLICY_PRESETS).map(([k, v]) => (
                <Button key={k} type="button" variant="ghost" className="h-6 px-2 text-[12px]" onClick={() => setRp({ ...rp, ...v })}>
                  {k}
                </Button>
              ))}
            </span>
          </legend>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Field label="Max attempts">
              <input type="number" min={1} max={15} className={inputCls} value={rp.max_attempts} onChange={(e) => setRp({ ...rp, max_attempts: num(e.target.value) })} />
            </Field>
            <Field label="Initial delay (ms)">
              <input type="number" min={100} className={inputCls} value={rp.initial_delay_ms} onChange={(e) => setRp({ ...rp, initial_delay_ms: num(e.target.value) })} />
            </Field>
            <Field label="Multiplier">
              <input type="number" min={1} max={10} step={0.5} className={inputCls} value={rp.multiplier} onChange={(e) => setRp({ ...rp, multiplier: num(e.target.value) })} />
            </Field>
            <Field label="Max delay (ms)">
              <input type="number" min={100} className={inputCls} value={rp.max_delay_ms} onChange={(e) => setRp({ ...rp, max_delay_ms: num(e.target.value) })} />
            </Field>
            <Field label="Jitter">
              <select className={inputCls} value={rp.jitter} onChange={(e) => setRp({ ...rp, jitter: e.target.value as RetryPolicy["jitter"] })}>
                <option value="full">Full (0 to delay)</option>
                <option value="equal">Equal (½ to full delay)</option>
                <option value="none">None</option>
              </select>
            </Field>
            <Field label="Retry network errors">
              <select className={inputCls} value={rp.retry_on_network_errors ? "y" : "n"} onChange={(e) => setRp({ ...rp, retry_on_network_errors: e.target.value === "y" })}>
                <option value="y">Yes</option>
                <option value="n">No</option>
              </select>
            </Field>
          </div>
          <Field label="Retryable status codes" hint="Other non-2xx responses are permanent failures and go straight to the dead-letter queue.">
            <input
              className={`${inputCls} font-mono`}
              value={rp.retry_on_status.join(", ")}
              onChange={(e) => setRp({ ...rp, retry_on_status: e.target.value.split(/[,\s]+/).filter(Boolean).map(Number).filter((n) => !Number.isNaN(n)) })}
            />
          </Field>
          <Schedule p={rp} />
        </fieldset>
      </div>

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

/** Plain-language schedule preview from the same formula the worker uses (spec §3.5). */
function Schedule({ p }: { p: RetryPolicy }) {
  const rows = [];
  let cumulative = 0;
  for (let n = 1; n < Math.min(p.max_attempts, 15); n++) {
    const base = Math.min(p.max_delay_ms, p.initial_delay_ms * p.multiplier ** (n - 1));
    const lo = p.jitter === "full" ? 0 : p.jitter === "equal" ? base / 2 : base;
    cumulative += base;
    rows.push({ n, lo, hi: base, cumulative });
  }
  return (
    <div className="rounded-sm bg-code p-3 text-[12px]">
      <p className="mb-2 text-ink-2">
        Up to <strong>{p.max_attempts}</strong> attempts. After each retryable failure, wait the delay below, then try
        again. After the last attempt, the delivery is dead-lettered.
      </p>
      {rows.length === 0 ? (
        <p className="text-ink-3">No retries: a single attempt only.</p>
      ) : (
        <ol className="grid grid-cols-[auto_1fr_auto] gap-x-4 gap-y-0.5 font-mono">
          {rows.map((r) => (
            <li key={r.n} className="contents">
              <span className="text-ink-3">#{r.n} → #{r.n + 1}</span>
              <span>{r.lo === r.hi ? fmtMs(r.hi) : `${fmtMs(Math.round(r.lo))} – ${fmtMs(r.hi)}`}</span>
              <span className="text-ink-3">≤ {fmtMs(r.cumulative)} total</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
