"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-ink border-accent hover:brightness-110",
  secondary: "bg-panel text-ink border-rule-strong hover:border-ink-3",
  ghost: "bg-transparent text-ink-2 border-transparent hover:text-ink hover:bg-panel",
  danger: "bg-panel text-bad border-rule-strong hover:border-bad",
};

export function Button({
  variant = "secondary",
  className = "",
  busy,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex h-8 items-center gap-1.5 rounded-sm border px-3 text-[13px] font-medium whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50 ${variants[variant]} ${className}`}
    >
      {busy ? <span className="pulse">…</span> : null}
      {children}
    </button>
  );
}

const STATUS: Record<string, { glyph: string; label: string; cls: string }> = {
  pending: { glyph: "○", label: "Pending", cls: "text-idle" },
  processing: { glyph: "◐", label: "Processing", cls: "text-busy" },
  retry_scheduled: { glyph: "↻", label: "Retry scheduled", cls: "text-warn" },
  succeeded: { glyph: "●", label: "Succeeded", cls: "text-ok" },
  dead_lettered: { glyph: "✕", label: "Dead-lettered", cls: "text-bad" },
  cancelled: { glyph: "⊘", label: "Cancelled", cls: "text-idle" },
  in_progress: { glyph: "◐", label: "In progress", cls: "text-busy" },
  delivered: { glyph: "●", label: "Delivered", cls: "text-ok" },
  partially_failed: { glyph: "◑", label: "Partially failed", cls: "text-warn" },
  failed: { glyph: "✕", label: "Failed", cls: "text-bad" },
  no_deliveries: { glyph: "○", label: "No deliveries", cls: "text-idle" },
  success: { glyph: "●", label: "Success", cls: "text-ok" },
  retryable_failure: { glyph: "↻", label: "Retryable failure", cls: "text-warn" },
  permanent_failure: { glyph: "✕", label: "Permanent failure", cls: "text-bad" },
};

export function Status({ value, compact }: { value: string; compact?: boolean }) {
  const s = STATUS[value] ?? { glyph: "?", label: value, cls: "text-ink-2" };
  const live = value === "processing" || value === "in_progress";
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap ${s.cls}`}>
      <span aria-hidden className={live ? "pulse" : ""}>
        {s.glyph}
      </span>
      <span className={compact ? "sr-only" : "text-[13px] font-medium"}>{s.label}</span>
    </span>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  if (!htmlFor) {
    // Without an explicit id, nest the control in the label so it is still programmatically labelled.
    return (
      <div className="flex flex-col gap-1">
        <label className="flex flex-col gap-1">
          <span className="text-[12px] font-medium text-ink-2">{label}</span>
          {children}
        </label>
        {hint ? <p className="text-[12px] text-ink-3">{hint}</p> : null}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-[12px] font-medium text-ink-2">
        {label}
      </label>
      {children}
      {hint ? <p className="text-[12px] text-ink-3">{hint}</p> : null}
    </div>
  );
}

export const inputCls =
  "h-8 w-full rounded-sm border border-rule-strong bg-panel px-2 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none";

export function ErrorNote({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p role="alert" className="border-l-2 border-bad bg-panel px-3 py-2 text-[13px] text-bad">
      {children}
    </p>
  );
}

export function SectionHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-2">
      <h2 className="text-[13px] font-semibold tracking-wide text-ink-2 uppercase">{title}</h2>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-[12px] ${className}`}>{children}</span>;
}

export function shortId(id: string) {
  return id.slice(0, 8);
}

export function fmtTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function fmtDateTime(iso: string) {
  const d = new Date(iso);
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${fmtTime(iso)}`;
}

export function fmtMs(ms: number) {
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(1)} min`;
  return `${(ms / 3_600_000).toFixed(1)} h`;
}

export function relTime(iso: string, now = Date.now()) {
  const diff = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(diff);
  const v = abs < 60 ? `${abs}s` : abs < 3600 ? `${Math.round(abs / 60)}m` : `${Math.round(abs / 3600)}h`;
  return diff >= 0 ? `in ${v}` : `${v} ago`;
}
