import { TryItNow } from "@/components/try-it-now";

const lifecycle = [
  { at: "t+0", glyph: "○", cls: "text-idle", text: "Event persisted, delivery queued in PostgreSQL" },
  { at: "t+0.1s", glyph: "↻", cls: "text-warn", text: "Attempt 1 → 503 from receiver. Retry scheduled with backoff + jitter" },
  { at: "t+0.6s", glyph: "↻", cls: "text-warn", text: "Attempt 2 → 503 again. Retry scheduled, delay doubled" },
  { at: "t+1.8s", glyph: "●", cls: "text-ok", text: "Attempt 3 → 200. Delivery succeeded, all 3 attempts kept" },
];

const workflows = [
  ["Break a receiver on purpose", "Make the built-in receiver return 503s, 429s with Retry-After, hang past your timeout, or drop the connection mid-response, then watch each retry happen."],
  ["Prove idempotency", "Lose acknowledgements or send the same key twice. The receiver records keys under a database unique constraint and shows which duplicates it suppressed."],
  ["Recover from the dead-letter queue", "Inspect exhausted deliveries, replay one or many with a different policy, and keep the original history linked to every replay."],
  ["Run a seeded experiment", "Pick one of eight scenarios, run hundreds of events, watch live progress, then change the retry policy, rerun with the same seed and compare."],
];

export default async function Landing({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const q = await searchParams;
  const notice = q.deleted ? "Your workspace and all of its data were deleted." : q.session === "expired" ? "Your guest session expired. Start a new one below." : null;
  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-14 px-4 py-10 sm:px-8 sm:py-16">
      {notice ? (
        <p role="status" className="border-l-2 border-accent bg-panel px-3 py-2 text-[13px]">
          {notice}
        </p>
      ) : null}
      <header className="flex items-center justify-between">
        <span className="font-mono text-[13px] text-ink-2">webhook reliability lab</span>
        <nav className="flex gap-4 text-[13px] text-ink-2">
          <a className="hover:text-ink" href="#how">
            How it works
          </a>
          <a className="hover:text-ink" href="#limits">
            Limits
          </a>
          <a className="hover:text-ink" href="/docs">
            Docs
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
            "Configure a receiver to fail, tune retry policies, send events or run seeded simulations, and inspect each attempt's request, response and timing. Analytics and exports come from the same stored records.",
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

      <section className="flex flex-col gap-4 border-t border-rule pt-8" aria-labelledby="workflows">
        <h2 id="workflows" className="text-[14px] font-semibold">
          Things to try in the first five minutes
        </h2>
        <ol className="grid gap-x-8 gap-y-4 md:grid-cols-2">
          {workflows.map(([h, p], i) => (
            <li key={h} className="grid grid-cols-[24px_1fr] gap-2">
              <span className="font-mono text-[13px] text-ink-3">{i + 1}.</span>
              <div>
                <h3 className="text-[13px] font-semibold">{h}</h3>
                <p className="text-[13px] text-ink-2">{p}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section id="limits" className="flex flex-col gap-3 border-t border-rule pt-8">
        <h2 className="text-[14px] font-semibold">Guest workspace limits</h2>
        <p className="max-w-prose text-[13px] text-ink-2">
          Guest workspaces are free and isolated. They are deleted after 24 hours of inactivity. Limits are enforced
          on the server: 5 endpoints, 256 KB payloads, hourly event and replay caps, one simulation at a time (up to
          1,000 events, 5 minutes), and 4 deliveries in flight per workspace. The built-in receiver covers every
          feature. The project is MIT-licensed open source and can be self-hosted without these shared limits. See
          the <a className="text-accent underline" href="/docs#self-hosting">self-hosting guide</a>.
        </p>
      </section>
    </main>
  );
}
