import { TryItNow } from "@/components/try-it-now";

const lifecycle = [
  { at: "t+0", glyph: "○", cls: "text-idle", text: "Event persisted, delivery queued in PostgreSQL" },
  { at: "t+0.1s", glyph: "↻", cls: "text-warn", text: "Attempt 1 → 503 from receiver. Retry scheduled with backoff + jitter" },
  { at: "t+0.6s", glyph: "↻", cls: "text-warn", text: "Attempt 2 → 503 again. Retry scheduled, delay doubled" },
  { at: "t+1.8s", glyph: "●", cls: "text-ok", text: "Attempt 3 → 200. Delivery succeeded, all 3 attempts kept" },
];

export default function Landing() {
  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-14 px-4 py-10 sm:px-8 sm:py-16">
      <header className="flex items-center justify-between">
        <span className="font-mono text-[13px] text-ink-2">webhook reliability lab</span>
        <nav className="flex gap-4 text-[13px] text-ink-2">
          <a className="hover:text-ink" href="#how">
            How it works
          </a>
          <a className="hover:text-ink" href="#limits">
            Limits
          </a>
        </nav>
      </header>

      <section className="grid gap-10 md:grid-cols-[1.1fr_1fr] md:items-end">
        <div className="flex flex-col gap-5">
          <h1 className="text-[32px] leading-tight font-semibold tracking-tight sm:text-[40px]">
            See exactly what happens to a webhook when delivery goes wrong.
          </h1>
          <p className="max-w-prose text-[15px] text-ink-2">
            Send events to a built-in receiver, make it fail on purpose, and inspect every attempt, retry, and
            dead-letter decision. A real delivery engine is running underneath. Nothing on screen is a mockup.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <TryItNow />
            <span className="text-[13px] text-ink-3">No sign-up. A private workspace is created for you.</span>
          </div>
        </div>
        <figure className="rounded-md border border-rule bg-panel p-4">
          <figcaption className="mb-3 text-[12px] text-ink-3">
            One delivery, as the event inspector records it (illustrative timeline)
          </figcaption>
          <ol className="flex flex-col gap-2">
            {lifecycle.map((l) => (
              <li key={l.at} className="grid grid-cols-[56px_16px_1fr] items-baseline gap-2 text-[13px]">
                <span className="font-mono text-[12px] text-ink-3">{l.at}</span>
                <span aria-hidden className={l.cls}>
                  {l.glyph}
                </span>
                <span>{l.text}</span>
              </li>
            ))}
          </ol>
        </figure>
      </section>

      <section id="how" className="grid gap-8 border-t border-rule pt-8 md:grid-cols-3">
        {[
          [
            "Why it's hard",
            "Webhook failures are invisible from the sender's side: timeouts, 429s and retries happen in queues you can't see, and duplicates surface days later in the receiver.",
          ],
          [
            "What you can do",
            "Configure a receiver to fail (always, first N attempts, or seeded-random), tune the retry policy, send events, and inspect each attempt's request, response and timing.",
          ],
          [
            "Honest semantics",
            "Delivery is at-least-once. Jobs are durable in PostgreSQL with worker leases, so crashes cause visible duplicate attempts, never silent loss.",
          ],
        ].map(([h, p]) => (
          <div key={h} className="flex flex-col gap-2">
            <h2 className="text-[14px] font-semibold">{h}</h2>
            <p className="text-[13px] text-ink-2">{p}</p>
          </div>
        ))}
      </section>

      <section id="limits" className="flex flex-col gap-3 border-t border-rule pt-8">
        <h2 className="text-[14px] font-semibold">Guest workspace limits</h2>
        <p className="max-w-prose text-[13px] text-ink-2">
          Guest workspaces are free and isolated. They are deleted after 24 hours of inactivity. Limits are enforced
          on the server: 5 endpoints, 256 KB payloads, and an hourly event cap. External HTTPS destinations are
          disabled on this deployment until SSRF protections ship. The built-in receiver covers every feature. The
          project is open source and self-hostable without these shared limits.
        </p>
      </section>
    </main>
  );
}
