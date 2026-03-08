"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { errorText, fetcher, post } from "@/lib/api";
import type { Endpoint, Preset, Session, SimulationConfig, SimulationRun } from "@/lib/types";
import { ReceiverFields, RetryPolicyFields } from "@/components/config-fields";
import { Button, ErrorNote, Field, SectionHeader, inputCls } from "@/components/ui";

type Draft = Omit<SimulationConfig, "endpoint_ids">;

export default function NewSimulationPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-3">Loading…</p>}>
      <Builder />
    </Suspense>
  );
}

function Builder() {
  const router = useRouter();
  const params = useSearchParams();
  const fromRun = params.get("from");
  const { data: presets } = useSWR<Preset[]>("/simulations/presets", fetcher);
  const { data: endpoints } = useSWR<Endpoint[]>("/endpoints", fetcher);
  const { data: session } = useSWR<Session>("/session", fetcher);
  const { data: source } = useSWR<SimulationRun>(fromRun ? `/simulations/${fromRun}` : null, fetcher);

  const [preset, setPreset] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [targets, setTargets] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const usable = useMemo(() => (endpoints ?? []).filter((e) => e.endpoint_type === "builtin" && e.enabled), [endpoints]);

  // Initialise once from ?from=<run> (rerun with edits) or ?preset=<key>.
  useEffect(() => {
    if (draft || !presets || !endpoints) return;
    if (fromRun) {
      if (!source) return;
      const { endpoint_ids, ...rest } = source.configuration;
      setDraft(rest);
      setPreset(source.preset);
      setTargets(endpoint_ids.filter((id) => usable.some((e) => e.id === id)));
      setName(`${source.name} (rerun)`);
      return;
    }
    const key = params.get("preset") ?? "intermittent_errors";
    const p = presets.find((x) => x.key === key) ?? presets[0];
    setPreset(p.key);
    setDraft({ ...p.config, seed: Math.floor(Math.random() * 100000) });
    setTargets(usable.slice(0, 1).map((e) => e.id));
    setName(p.title);
  }, [draft, presets, endpoints, fromRun, source, params, usable]);

  if (!draft || !presets) return <p className="text-[13px] text-ink-3">Loading simulation builder…</p>;

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft({ ...draft, [k]: v });
  const num = (v: string) => (v === "" ? 0 : Number(v));
  const maxConcurrency = session?.limits.workspace_concurrency ?? 4;
  const maxEvents = session?.limits.simulation_events ?? 1000;
  const maxRuntime = session?.limits.simulation_runtime_seconds ?? 300;

  function choose(p: Preset) {
    setPreset(p.key);
    setName(p.title);
    // Keep the experiment's size and seed; take every behavior setting from the preset.
    setDraft({ ...p.config, event_count: draft!.event_count, seed: draft!.seed, concurrency: Math.min(draft!.concurrency, maxConcurrency) });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await post<SimulationRun>("/simulations", { name: name.trim() || null, preset, config: { ...draft, endpoint_ids: targets } });
      router.push(`/app/simulations/${run.id}`);
    } catch (x) {
      setError(errorText(x));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[18px] font-semibold">{source ? "Rerun simulation" : "New simulation"}</h1>
        <Button type="submit" variant="primary" busy={busy} disabled={targets.length === 0}>
          Start simulation
        </Button>
      </div>
      {source ? (
        <p className="text-[13px] text-ink-2">
          Starting from the configuration of <strong>{source.name}</strong>. Change one setting, such as the retry policy,
          keep the seed, and compare the two runs afterwards.
        </p>
      ) : null}
      <ErrorNote>{error}</ErrorNote>

      <section className="flex flex-col gap-2">
        <SectionHeader title="Scenario" />
        <div role="radiogroup" aria-label="Scenario preset" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {presets.map((p) => (
            <button
              key={p.key}
              type="button"
              role="radio"
              aria-checked={preset === p.key}
              onClick={() => choose(p)}
              className={`flex flex-col gap-1 rounded-md border p-3 text-left ${preset === p.key ? "border-accent bg-panel" : "border-rule hover:border-ink-3"}`}
            >
              <span className="text-[13px] font-semibold">{p.title}</span>
              <span className="text-[12px] text-ink-2">{p.description}</span>
            </button>
          ))}
        </div>
        <p className="text-[12px] text-ink-3">A preset only fills in the settings below. Everything you see is exactly what runs.</p>
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex flex-col gap-6">
          <section className="flex flex-col gap-3">
            <SectionHeader title="Run" />
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Field label="Name">
                <input className={inputCls} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label={`Events (max ${maxEvents})`}>
                <input type="number" min={1} max={maxEvents} className={inputCls} value={draft.event_count} onChange={(e) => set("event_count", num(e.target.value))} />
              </Field>
              <Field label="Seed" htmlFor="run-seed" hint="Same seed + same settings = same failure decisions.">
                <div className="flex gap-1">
                  <input id="run-seed" type="number" min={0} className={inputCls} value={draft.seed} onChange={(e) => set("seed", num(e.target.value))} />
                  <Button type="button" variant="ghost" onClick={() => set("seed", Math.floor(Math.random() * 100000))} aria-label="Pick a new random seed">
                    New
                  </Button>
                </div>
              </Field>
              <Field label={`Concurrency (max ${maxConcurrency})`} hint="Deliveries in flight at once.">
                <input type="number" min={1} max={maxConcurrency} className={inputCls} value={draft.concurrency} onChange={(e) => set("concurrency", num(e.target.value))} />
              </Field>
              <Field label="Release events over (ms)" hint="0 sends them all at once.">
                <input type="number" min={0} max={60000} step={500} className={inputCls} value={draft.emit_over_ms} onChange={(e) => set("emit_over_ms", num(e.target.value))} />
              </Field>
              <Field label={`Max runtime (s, max ${maxRuntime})`} hint="Unfinished deliveries are cancelled at this limit.">
                <input type="number" min={10} max={maxRuntime} className={inputCls} value={draft.max_runtime_s} onChange={(e) => set("max_runtime_s", num(e.target.value))} />
              </Field>
              <Field label="Producer duplicates (%)" hint="Events sent twice with the same idempotency key.">
                <input type="number" min={0} max={50} className={inputCls} value={Math.round(draft.duplicate_send_rate * 100)} onChange={(e) => set("duplicate_send_rate", num(e.target.value) / 100)} />
              </Field>
              <Field label="Event type">
                <input className={`${inputCls} font-mono`} value={draft.event_type} maxLength={120} onChange={(e) => set("event_type", e.target.value)} />
              </Field>
              <label className="flex items-start gap-2 pt-5 text-[13px]">
                <input type="checkbox" checked={draft.use_idempotency_keys} onChange={(e) => set("use_idempotency_keys", e.target.checked)} />
                Send idempotency keys
              </label>
            </div>
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-[12px] font-medium text-ink-2">Target receivers</legend>
              {usable.length === 0 ? <p className="text-[12px] text-bad">No enabled built-in receivers. Enable or create one on the Endpoints page.</p> : null}
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {usable.map((ep) => (
                  <label key={ep.id} className="flex items-center gap-2 text-[13px]">
                    <input
                      type="checkbox"
                      checked={targets.includes(ep.id)}
                      onChange={(e) => setTargets((t) => (e.target.checked ? [...t, ep.id] : t.filter((x) => x !== ep.id)))}
                    />
                    {ep.name}
                  </label>
                ))}
              </div>
              <p className="text-[12px] text-ink-3">
                During the run each receiver applies the simulation&apos;s behavior below, not its own saved settings.
              </p>
            </fieldset>
          </section>

          <div className="grid gap-6 xl:grid-cols-2">
            <ReceiverFields id="sim" rc={draft.receiver} setRc={(r) => set("receiver", r)} seedFromRun />
            <RetryPolicyFields rp={draft.retry_policy} setRp={(p) => set("retry_policy", p)} />
          </div>
        </div>

        <aside className="flex flex-col gap-2 lg:sticky lg:top-4 lg:self-start" aria-label="What will happen">
          <Expectation draft={draft} targets={targets.length} />
        </aside>
      </div>
    </form>
  );
}

/** Plain-language prediction derived from the configuration only. The results show what actually happened. */
function Expectation({ draft, targets }: { draft: Draft; targets: number }) {
  const r = draft.receiver;
  const p = draft.retry_policy;
  const deliveries = Math.round(draft.event_count * (1 + draft.duplicate_send_rate)) * targets;
  const fail = r.mode === "random" ? r.failure_rate : r.mode === "fail" ? 1 : 0;
  const statusFailureRetryable = p.retry_on_status.includes(r.failure_status);
  const lines: string[] = [];
  if (r.mode === "succeed") lines.push("Every attempt is answered with 200 OK.");
  if (r.mode === "fail") lines.push("Every attempt fails, so every delivery ends in the dead-letter queue.");
  if (r.mode === "fail_first_n")
    lines.push(
      r.fail_first_n < p.max_attempts
        ? `Each delivery fails ${r.fail_first_n} time(s), then succeeds on attempt ${r.fail_first_n + 1}.`
        : `Each delivery fails more times (${r.fail_first_n}) than the policy allows (${p.max_attempts}), so all are dead-lettered.`,
    );
  if (r.mode === "random") {
    lines.push(`Each attempt independently fails with ${Math.round(fail * 100)}% probability.`);
    lines.push(
      `If every failure is retried, about ${(fail ** p.max_attempts * 100).toFixed(fail ** p.max_attempts < 0.01 ? 2 : 1)}% of deliveries fail all ${p.max_attempts} attempts and are dead-lettered.`,
    );
    if (r.failure_mix.server_error > 0 && !statusFailureRetryable)
      lines.push(`HTTP ${r.failure_status} is not in the retryable list, so those failures dead-letter immediately.`);
    if (r.failure_mix.timeout > 0) lines.push(`Timeouts cost ${p.timeout_ms} ms each before a retry is scheduled.`);
    if (r.failure_mix.rate_limited > 0 && p.respect_retry_after) lines.push(`429 responses delay the next retry by at least ${r.retry_after_s} s.`);
  }
  if (r.ack_loss_rate > 0)
    lines.push(`${Math.round(r.ack_loss_rate * 100)}% of successful attempts lose their acknowledgement, so the sender retries a delivery that already succeeded.`);
  if (draft.duplicate_send_rate > 0) lines.push(`About ${Math.round(draft.duplicate_send_rate * 100)}% of events are sent twice by the producer.`);
  if (r.ack_loss_rate > 0 || draft.duplicate_send_rate > 0)
    lines.push(r.idempotency && draft.use_idempotency_keys ? "The idempotent receiver should suppress these duplicates." : "Without idempotency these duplicates are processed again.");
  return (
    <div className="flex flex-col gap-2 rounded-md border border-rule bg-panel p-4 text-[13px]">
      <h2 className="text-[13px] font-semibold">Before you run</h2>
      <p className="text-ink-2">
        About <strong>{deliveries}</strong> deliveries ({draft.event_count} events × {targets} receiver{targets === 1 ? "" : "s"}
        {draft.duplicate_send_rate > 0 ? " plus duplicates" : ""}), up to {draft.concurrency} at a time.
      </p>
      <ul className="flex list-disc flex-col gap-1 pl-4 text-ink-2">
        {lines.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
      <p className="text-[12px] text-ink-3">This is a prediction from the settings. The results page shows what was actually recorded.</p>
    </div>
  );
}
