"use client";

import { useState } from "react";
import { errorText, post } from "@/lib/api";
import type { DeliverySummary, ReplayPolicy } from "@/lib/types";
import { Button, ErrorNote, inputCls } from "./ui";

export const REPLAY_POLICIES: { value: ReplayPolicy; label: string }[] = [
  { value: "original", label: "Original policy" },
  { value: "endpoint", label: "Endpoint's current policy" },
  { value: "fast", label: "Fast preset" },
  { value: "balanced", label: "Balanced preset" },
  { value: "conservative", label: "Conservative preset" },
];

export function PolicySelect({ value, onChange, id }: { value: ReplayPolicy; onChange: (v: ReplayPolicy) => void; id?: string }) {
  return (
    <select
      id={id}
      aria-label="Retry policy for the replay"
      className={`${inputCls} w-auto`}
      value={value}
      onChange={(e) => onChange(e.target.value as ReplayPolicy)}
    >
      {REPLAY_POLICIES.map((p) => (
        <option key={p.value} value={p.value}>
          {p.label}
        </option>
      ))}
    </select>
  );
}

/** Replays one delivery as a new, linked delivery. The original and its attempts are never modified. */
export function ReplayControl({ path, onDone, label = "Replay" }: { path: string; onDone: () => void; label?: string }) {
  const [policy, setPolicy] = useState<ReplayPolicy>("original");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <PolicySelect value={policy} onChange={setPolicy} />
        <Button
          busy={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await post<{ replays: DeliverySummary[] }>(path, { policy });
              onDone();
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {label}
        </Button>
      </div>
      <ErrorNote>{error}</ErrorNote>
    </div>
  );
}
