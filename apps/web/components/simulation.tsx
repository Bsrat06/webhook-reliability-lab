"use client";

import type { Percentiles, SimulationConfig, SimulationRun, SimulationSummary } from "@/lib/types";
import { BarList, StatusBar } from "./charts";
import { Mono, Status, fmtMs } from "./ui";

export const DELIVERY_ORDER = ["succeeded", "dead_lettered", "cancelled", "retry_scheduled", "processing", "pending"];

export function pct(v: number | null | undefined) {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(v === 1 || v === 0 ? 0 : 1)}%`;
}

export function RunStatus({ run }: { run: Pick<SimulationRun, "status"> }) {
  return <Status value={run.status} />;
}

export function PercentileRow({ label, p }: { label: string; p: Percentiles }) {
  return (
    <div className="grid grid-cols-[1fr_repeat(3,64px)] items-baseline gap-2 text-[12px]">
      <span className="text-ink-2">
        {label} <span className="text-ink-3">n={p.n}</span>
      </span>
      {p.insufficient ? (
        <span className="col-span-3 text-right text-ink-3">needs ≥ 20 samples</span>
      ) : (
        (["p50", "p95", "p99"] as const).map((k) => (
          <span key={k} className="text-right font-mono">
            <span className="text-ink-3">{k} </span>
            {fmtMs(p[k] ?? 0)}
          </span>
        ))
      )}
    </div>
  );
}

const CATEGORY_LABEL: Record<string, string> = {
  success: "Success",
  http_5xx: "HTTP 5xx",
  http_4xx: "HTTP 4xx",
  rate_limited: "429 rate limited",
  timeout: "Timeout",
  connection_reset: "Connection dropped",
  connection_error: "Connect failed",
  network_error: "Network error",
};

/** Every number here comes from the run's stored deliveries and attempts. */
export function SummaryPanel({ s }: { s: SimulationSummary }) {
  const stats: [string, string, string][] = [
    ["Success rate", pct(s.success_rate), "succeeded ÷ (succeeded + dead-lettered); cancelled excluded"],
    ["Dead-letter rate", pct(s.dead_letter_rate), "dead-lettered ÷ (succeeded + dead-lettered)"],
    ["Attempts", String(s.attempts), "HTTP attempts recorded"],
    ["Retries", String(s.retries), "attempts beyond the first per delivery"],
    ["Attempts / delivery", s.attempts_per_delivery?.toFixed(2) ?? "—", "over deliveries with at least one attempt"],
    ["Duplicates suppressed", String(s.duplicates_suppressed), "attempts the idempotent receiver recognised as already processed"],
  ];
  const categories = Object.entries(s.failure_categories)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ label: CATEGORY_LABEL[k] ?? k, value: v }));
  const distribution = Object.entries(s.attempt_distribution).map(([k, v]) => ({
    label: `${k} attempt${k === "1" ? "" : "s"}`,
    value: v,
  }));
  return (
    <div className="flex flex-col gap-5">
      <StatusBar counts={s.by_status} total={s.deliveries} order={DELIVERY_ORDER} />
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
        {stats.map(([k, v, hint]) => (
          <div key={k} title={hint}>
            <dt className="text-[12px] text-ink-3">{k}</dt>
            <dd className="font-mono text-[18px] leading-tight">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="grid gap-5 md:grid-cols-2">
        <section className="flex flex-col gap-2" aria-label="Attempt outcomes">
          <h3 className="text-[12px] font-medium text-ink-2">Attempt outcomes</h3>
          <BarList rows={categories} empty="No attempts recorded yet." />
        </section>
        <section className="flex flex-col gap-2" aria-label="Attempts per finished delivery">
          <h3 className="text-[12px] font-medium text-ink-2">Attempts per finished delivery</h3>
          <BarList rows={distribution} empty="No finished deliveries yet." />
        </section>
      </div>
      <section className="flex flex-col gap-1" aria-label="Latency">
        <h3 className="text-[12px] font-medium text-ink-2">Latency</h3>
        <PercentileRow label="Attempt duration" p={s.latency_ms} />
        <PercentileRow label="Created → delivered" p={s.delivery_time_ms} />
      </section>
    </div>
  );
}

export function ConfigSummary({ c, preset }: { c: SimulationConfig; preset?: string | null }) {
  const r = c.receiver;
  const p = c.retry_policy;
  const mix = Object.entries(r.failure_mix ?? {})
    .filter(([, w]) => w > 0)
    .map(([k]) => k.replace("_", " "))
    .join(", ");
  const rows: [string, React.ReactNode][] = [
    ["Preset", preset ?? "custom"],
    ["Seed", <Mono key="s">{c.seed}</Mono>],
    ["Events", `${c.event_count}${c.duplicate_send_rate ? ` + ${Math.round(c.duplicate_send_rate * 100)}% producer duplicates` : ""}, released over ${fmtMs(c.emit_over_ms)}`],
    ["Targets", `${c.endpoint_ids.length} built-in receiver${c.endpoint_ids.length === 1 ? "" : "s"}, concurrency ${c.concurrency}`],
    [
      "Receiver",
      r.mode === "random"
        ? `${Math.round(r.failure_rate * 100)}% of attempts fail (${mix})`
        : r.mode === "succeed"
          ? "always succeeds"
          : r.mode === "fail"
            ? `always fails (${r.failure_kind.replace("_", " ")})`
            : `fails the first ${r.fail_first_n} attempts`,
    ],
    ["Latency", `${r.latency_ms_min}–${r.latency_ms_max} ms`],
    ["Lost acks", `${Math.round(r.ack_loss_rate * 100)}%, idempotent receiver ${r.idempotency ? "on" : "off"}`],
    ["Retry policy", `${p.max_attempts} attempts, ${fmtMs(p.initial_delay_ms)} ×${p.multiplier} up to ${fmtMs(p.max_delay_ms)}, ${p.jitter} jitter`],
    ["Timeout", `${fmtMs(p.timeout_ms)}; Retry-After ${p.respect_retry_after ? "honored" : "ignored"}`],
    ["Max runtime", `${c.max_runtime_s} s`],
  ];
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
