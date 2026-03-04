"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { errorText, post } from "@/lib/api";
import type { Endpoint, EventDetail } from "@/lib/types";
import { Button, ErrorNote, Field, inputCls } from "./ui";

const SAMPLES: Record<string, object> = {
  "order.created": { order_id: "ord_1042", amount_cents: 4999, currency: "USD", items: 2 },
  "invoice.payment_failed": { invoice_id: "inv_88", attempt: 1, reason: "card_declined" },
  "user.signed_up": { user_id: "usr_301", plan: "free", referrer: null },
};

export function SendEventForm({ endpoints, onSent }: { endpoints: Endpoint[]; onSent?: () => void }) {
  const router = useRouter();
  const enabled = endpoints.filter((e) => e.enabled);
  const [eventType, setEventType] = useState("order.created");
  const [payload, setPayload] = useState(JSON.stringify(SAMPLES["order.created"], null, 2));
  const [selected, setSelected] = useState<string[]>(enabled.slice(0, 1).map((e) => e.id));
  const [idemKey, setIdemKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsed = useMemo(() => {
    try {
      const v = JSON.parse(payload);
      if (typeof v !== "object" || v === null || Array.isArray(v)) return { error: "Payload must be a JSON object." };
      return { value: v as Record<string, unknown> };
    } catch (e) {
      return { error: `Invalid JSON: ${(e as Error).message}` };
    }
  }, [payload]);

  async function submit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!parsed.value) return;
    setBusy(true);
    setError(null);
    try {
      const created = await post<EventDetail>("/events", {
        event_type: eventType,
        payload: parsed.value,
        endpoint_ids: selected,
        idempotency_key: idemKey.trim() || null,
      });
      onSent?.();
      router.push(`/app/events/${created.id}`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4 rounded-md border border-rule bg-panel p-4 md:grid-cols-[1fr_280px]">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-48 flex-1">
            <Field label="Event type" htmlFor="event-type">
              <input id="event-type" className={inputCls} value={eventType} onChange={(e) => setEventType(e.target.value)} required maxLength={120} pattern="[A-Za-z0-9_.:\-]+" />
            </Field>
          </div>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Sample payloads">
            {Object.keys(SAMPLES).map((k) => (
              <Button
                key={k}
                type="button"
                variant="ghost"
                className="font-mono text-[12px]"
                onClick={() => {
                  setEventType(k);
                  setPayload(JSON.stringify(SAMPLES[k], null, 2));
                }}
              >
                {k}
              </Button>
            ))}
          </div>
        </div>
        <Field label="Payload (JSON)" htmlFor="payload" hint={parsed.error ? <span className="text-bad">{parsed.error}</span> : "Max 256 KB."}>
          <textarea
            id="payload"
            spellCheck={false}
            className={`${inputCls} h-48 resize-y py-2 font-mono text-[12px] leading-relaxed`}
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
            aria-invalid={!!parsed.error}
          />
        </Field>
      </div>
      <div className="flex flex-col gap-3">
        <fieldset className="flex flex-col gap-1">
          <legend className="mb-1 text-[12px] font-medium text-ink-2">Deliver to</legend>
          {enabled.length === 0 ? <p className="text-[12px] text-ink-3">No enabled endpoints. Enable one on the Endpoints page.</p> : null}
          {enabled.map((ep) => (
            <label key={ep.id} className="flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={selected.includes(ep.id)}
                onChange={(e) => setSelected((s) => (e.target.checked ? [...s, ep.id] : s.filter((x) => x !== ep.id)))}
              />
              {ep.name}
              <span className="text-[12px] text-ink-3">{ep.receiver_config.mode.replaceAll("_", " ")}</span>
            </label>
          ))}
        </fieldset>
        <Field label="Idempotency key (optional)" htmlFor="idem" hint="Sent as the Idempotency-Key header on every attempt.">
          <input id="idem" className={`${inputCls} font-mono`} value={idemKey} onChange={(e) => setIdemKey(e.target.value)} maxLength={200} />
        </Field>
        <ErrorNote>{error}</ErrorNote>
        <Button type="submit" variant="primary" busy={busy} disabled={!parsed.value || selected.length === 0} className="self-start">
          Send event
        </Button>
      </div>
    </form>
  );
}
