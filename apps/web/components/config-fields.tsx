"use client";

import type { FailureKind, FailureMix, ReceiverConfig, RetryPolicy } from "@/lib/types";
import { Button, Field, fmtMs, inputCls } from "./ui";

const FAILURE_KINDS: { value: FailureKind; label: string }[] = [
  { value: "server_error", label: "HTTP error status" },
  { value: "rate_limited", label: "429 rate limited (with Retry-After)" },
  { value: "timeout", label: "Timeout (never answers in time)" },
  { value: "connection_drop", label: "Connection dropped mid-response" },
];

const POLICY_PRESETS: Record<string, Partial<RetryPolicy>> = {
  Fast: { max_attempts: 4, initial_delay_ms: 500, multiplier: 2, max_delay_ms: 4000, jitter: "full" },
  Balanced: { max_attempts: 6, initial_delay_ms: 2000, multiplier: 2, max_delay_ms: 60000, jitter: "full" },
  Conservative: { max_attempts: 8, initial_delay_ms: 10000, multiplier: 3, max_delay_ms: 3600000, jitter: "equal" },
};

const num = (v: string) => (v === "" ? 0 : Number(v));

/** Built-in receiver behavior editor, shared by endpoints and the simulation builder. */
export function ReceiverFields({
  id,
  rc,
  setRc,
  seedFromRun,
}: {
  id: string;
  rc: ReceiverConfig;
  setRc: (r: ReceiverConfig) => void;
  /** Simulations seed the receiver from the run's seed, so its own seed field is hidden. */
  seedFromRun?: boolean;
}) {
  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="mb-2 text-[13px] font-semibold">Receiver behavior</legend>
      <Field label="Responds with" htmlFor={`${id}-mode`}>
        <select id={`${id}-mode`} className={inputCls} value={rc.mode} onChange={(e) => setRc({ ...rc, mode: e.target.value as ReceiverConfig["mode"] })}>
          <option value="succeed">Always 200 OK</option>
          <option value="fail">Always fail</option>
          <option value="fail_first_n">Fail the first N attempts, then 200</option>
          <option value="random">Fail randomly (seeded)</option>
        </select>
      </Field>
      {rc.mode === "fail" || rc.mode === "fail_first_n" ? (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Fails by" htmlFor={`${id}-kind`}>
            <select id={`${id}-kind`} className={inputCls} value={rc.failure_kind} onChange={(e) => setRc({ ...rc, failure_kind: e.target.value as FailureKind })}>
              {FAILURE_KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
          </Field>
          {rc.failure_kind === "server_error" ? <StatusSelect id={`${id}-status`} rc={rc} setRc={setRc} /> : null}
          {rc.mode === "fail_first_n" ? (
            <Field label="Failing attempts (N)" htmlFor={`${id}-n`}>
              <input id={`${id}-n`} type="number" min={0} max={15} className={inputCls} value={rc.fail_first_n} onChange={(e) => setRc({ ...rc, fail_first_n: num(e.target.value) })} />
            </Field>
          ) : null}
        </div>
      ) : null}
      {rc.mode === "random" ? (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Failure rate (%)" htmlFor={`${id}-rate`}>
              <input id={`${id}-rate`} type="number" min={0} max={100} className={inputCls} value={Math.round(rc.failure_rate * 100)} onChange={(e) => setRc({ ...rc, failure_rate: num(e.target.value) / 100 })} />
            </Field>
            {seedFromRun ? null : (
              <Field label="Seed" htmlFor={`${id}-seed`} hint="Same seed, event and attempt give the same decision.">
                <input id={`${id}-seed`} type="number" min={0} className={inputCls} value={rc.seed} onChange={(e) => setRc({ ...rc, seed: num(e.target.value) })} />
              </Field>
            )}
            <StatusSelect id={`${id}-status`} rc={rc} setRc={setRc} />
          </div>
          <MixEditor mix={rc.failure_mix} onChange={(m) => setRc({ ...rc, failure_mix: m })} />
        </>
      ) : null}
      {rc.mode !== "succeed" ? (
        <Field label="Retry-After on 429 (seconds)" htmlFor={`${id}-ra`}>
          <input id={`${id}-ra`} type="number" min={0} max={30} className={inputCls} value={rc.retry_after_s} onChange={(e) => setRc({ ...rc, retry_after_s: num(e.target.value) })} />
        </Field>
      ) : null}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Latency min (ms)" htmlFor={`${id}-lmin`}>
          <input id={`${id}-lmin`} type="number" min={0} max={5000} className={inputCls} value={rc.latency_ms_min} onChange={(e) => setRc({ ...rc, latency_ms_min: num(e.target.value) })} />
        </Field>
        <Field label="Latency max (ms)" htmlFor={`${id}-lmax`}>
          <input id={`${id}-lmax`} type="number" min={0} max={5000} className={inputCls} value={rc.latency_ms_max} onChange={(e) => setRc({ ...rc, latency_ms_max: num(e.target.value) })} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Lost acknowledgements (%)" htmlFor={`${id}-ack`} hint="Processed, but the response is dropped, so the sender retries a delivery that already succeeded.">
          <input id={`${id}-ack`} type="number" min={0} max={100} className={inputCls} value={Math.round(rc.ack_loss_rate * 100)} onChange={(e) => setRc({ ...rc, ack_loss_rate: num(e.target.value) / 100 })} />
        </Field>
        <label className="flex items-start gap-2 pt-5 text-[13px]">
          <input type="checkbox" checked={rc.idempotency} onChange={(e) => setRc({ ...rc, idempotency: e.target.checked })} />
          <span>
            Idempotent receiver
            <span className="block text-[12px] text-ink-3">Deduplicates on the Idempotency-Key header, or the event ID.</span>
          </span>
        </label>
      </div>
    </fieldset>
  );
}

/** Retry policy editor with a plain-language schedule preview. */
export function RetryPolicyFields({ rp, setRp }: { rp: RetryPolicy; setRp: (p: RetryPolicy) => void }) {
  return (
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
        <Field label="Request timeout (ms)">
          <input type="number" min={200} max={10000} step={100} className={inputCls} value={rp.timeout_ms} onChange={(e) => setRp({ ...rp, timeout_ms: num(e.target.value) })} />
        </Field>
        <Field label="Honor Retry-After">
          <select className={inputCls} value={rp.respect_retry_after ? "y" : "n"} onChange={(e) => setRp({ ...rp, respect_retry_after: e.target.value === "y" })}>
            <option value="y">Yes (capped at max delay)</option>
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
  );
}

function StatusSelect({ id, rc, setRc }: { id: string; rc: ReceiverConfig; setRc: (r: ReceiverConfig) => void }) {
  return (
    <Field label="Error status" htmlFor={id} hint={rc.failure_status < 500 ? "4xx responses are not retried by default." : undefined}>
      <select id={id} className={inputCls} value={rc.failure_status} onChange={(e) => setRc({ ...rc, failure_status: Number(e.target.value) })}>
        {[500, 502, 503, 504, 408, 400, 401, 404, 410, 422].map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    </Field>
  );
}

function MixEditor({ mix, onChange }: { mix: FailureMix; onChange: (m: FailureMix) => void }) {
  const total = Object.values(mix).reduce((a, b) => a + b, 0);
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-[12px] font-medium text-ink-2">When an attempt fails, it fails as (relative weights)</legend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {FAILURE_KINDS.map((k) => (
          <label key={k.value} className="flex flex-col gap-0.5 text-[12px] text-ink-2">
            {k.label.split(" (")[0]}
            <input
              type="number"
              min={0}
              max={100}
              className={inputCls}
              value={mix[k.value]}
              onChange={(e) => onChange({ ...mix, [k.value]: e.target.value === "" ? 0 : Number(e.target.value) })}
            />
            <span className="font-mono text-ink-3">{total > 0 ? Math.round((mix[k.value] / total) * 100) : 0}%</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** Plain-language schedule preview from the same formula the worker uses (spec §3.5). */
export function Schedule({ p }: { p: RetryPolicy }) {
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
