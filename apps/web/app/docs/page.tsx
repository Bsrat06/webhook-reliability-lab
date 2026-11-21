import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Docs · Webhook Reliability Lab" };

const SECTIONS: { id: string; title: string; body: ReactNode }[] = [
  {
    id: "getting-started",
    title: "Getting started",
    body: (
      <>
        <p>
          Click <strong>Try it now</strong> on the home page. A private guest workspace is created with one built-in
          receiver, and your session lives in an HttpOnly cookie in this browser. A quick tour:
        </p>
        <ol>
          <li>Open <em>Simulations → New simulation</em>, pick <em>Intermittent server errors</em> and start it.</li>
          <li>Watch progress stream in, then filter the run&apos;s events to <em>Retried</em> and open one to see each attempt.</li>
          <li>Click <em>Rerun with changes</em>, lower <em>Max attempts</em> to 2, keep the seed, and run again.</li>
          <li>Select both runs on the Simulations page and compare them.</li>
        </ol>
      </>
    ),
  },
  {
    id: "endpoints",
    title: "Creating an endpoint",
    body: (
      <>
        <p>
          Built-in receivers are real HTTP endpoints hosted by the platform. The worker signs every request with
          HMAC-SHA256 (<code>x-webhook-signature: t=…,v1=…</code>) and the receiver verifies it before responding. The
          signing secret is shown once at creation or rotation and is stored encrypted.
        </p>
        <p>
          Each receiver has a <em>behavior</em>: always succeed, always fail, fail the first N attempts, or fail
          randomly from a seed. Failures can be an HTTP error status, a 429 with <code>Retry-After</code>, a timeout
          (the receiver never answers in time), or a connection dropped mid-response. <em>Lost acknowledgements</em>{" "}
          make the receiver process a request and then drop the response, which is how duplicates happen in real
          systems.
        </p>
      </>
    ),
  },
  {
    id: "events",
    title: "Sending an event",
    body: (
      <p>
        On <em>Events</em>, choose a type, write a JSON payload (256 KB max), pick receivers and optionally set an
        idempotency key. The event, one delivery per receiver and their first jobs are written in one database
        transaction, then the worker sends them asynchronously. The inspector shows the timeline, every attempt&apos;s
        headers, status, body excerpt and timing, and the state transitions.
      </p>
    ),
  },
  {
    id: "states",
    title: "Delivery states",
    body: (
      <>
        <ul>
          <li><strong>Pending</strong>: stored and queued, not yet picked up.</li>
          <li><strong>Processing</strong>: a worker holds a lease on the job and is sending it.</li>
          <li><strong>Retry scheduled</strong>: the last attempt failed with a retryable outcome; the next one is due at a stored time.</li>
          <li><strong>Succeeded</strong>: a 2xx response was received.</li>
          <li><strong>Dead-lettered</strong>: retries ran out or the failure was not retryable.</li>
          <li><strong>Cancelled</strong>: stopped by you, by a simulation cancel or runtime limit, or because the endpoint was disabled.</li>
        </ul>
        <p>
          Delivery is <strong>at-least-once</strong>. If a worker crashes after sending but before recording the
          result, its lease expires and the attempt is repeated. The duplicate is visible in the history. Exactly-once
          delivery is not claimed. Receivers should be idempotent.
        </p>
      </>
    ),
  },
  {
    id: "retries",
    title: "Retry policies",
    body: (
      <p>
        A policy sets max attempts, an initial delay, a multiplier, a delay cap, jitter (none, full or equal), the
        retryable status codes, whether network errors are retried, a request timeout, and whether to honor{" "}
        <code>Retry-After</code>. The delay before retry <em>n</em> is <code>min(cap, initial × multiplier^(n−1))</code>,
        then jittered. Retries are scheduled as durable jobs; no worker sleeps. Each delivery keeps a snapshot of the
        policy it started with, so editing an endpoint never changes deliveries already in flight.
      </p>
    ),
  },
  {
    id: "idempotency",
    title: "Idempotency",
    body: (
      <p>
        When idempotency is on, the built-in receiver deduplicates on the <code>Idempotency-Key</code> header, or on
        the event ID if there is none. Keys are scoped to one receiver and kept for 24 hours. A database unique
        constraint on (receiver, key) decides which request processes first, so even concurrent duplicates are
        processed once. Each attempt shows the receiver&apos;s verdict (processed, duplicate suppressed, rejected), and
        the endpoint page lists processed keys. This demonstrates the pattern; the platform cannot make a third-party
        receiver idempotent.
      </p>
    ),
  },
  {
    id: "dlq",
    title: "Dead-letter queue and replay",
    body: (
      <>
        <p>
          Exhausted or permanently failed deliveries land in the dead-letter queue with the reason and full attempt
          history. Nothing is discarded. From there you can replay, or dismiss with a note. Both are recorded in the
          audit log.
        </p>
        <p>
          A replay is a <em>new</em> delivery linked to the original (<code>replay_of</code>). It uses the original
          policy, the endpoint&apos;s current policy, or a preset. Bulk replay is capped at 50 deliveries and needs explicit
          confirmation, and replays have an hourly quota to prevent replay storms. An event&apos;s status is judged by the
          latest delivery in each replay chain.
        </p>
      </>
    ),
  },
  {
    id: "simulations",
    title: "Running and comparing simulations",
    body: (
      <>
        <p>
          A simulation creates real events and deliveries and sends them through the same worker and receivers. Its
          receiver behavior, retry policy, event count, concurrency, release window, producer duplicates and runtime
          limit are stored with the run. The eight presets are just filled-in configurations you can edit.
        </p>
        <p>
          Failure decisions are a pure function of the seed, the event&apos;s position and the attempt number, and retry
          jitter is seeded too. The same seed and settings reproduce the same decisions. Wall-clock timings and
          latencies still vary with load. Progress streams over server-sent events, with automatic reconnection and a
          polling fallback; the database is always the source of truth. Results are computed from the stored
          deliveries and attempts. Percentiles are shown only with at least 20 samples.
        </p>
        <p>
          The comparison view lines up outcomes and lists every configuration difference. Different seeds mean
          different failure decisions, so for a fair policy comparison keep the seed. Small runs are noisy; the
          comparison makes no claim of statistical significance.
        </p>
      </>
    ),
  },
  {
    id: "analytics",
    title: "Analytics and export",
    body: (
      <p>
        Analytics are computed on request from your stored records, for a time window and source (manual or
        simulation). Each figure states its denominator. &ldquo;n/a&rdquo; means there is not enough data, which is
        different from zero. Exports (CSV or JSON) cover events, deliveries, attempts, dead letters, simulations and an
        analytics summary. They are capped at 10,000 rows and say when they are truncated. Secrets and request headers
        are never exported.
      </p>
    ),
  },
  {
    id: "external",
    title: "External endpoint safety",
    body: (
      <p>
        External HTTPS destinations are a server-side request forgery (SSRF) risk on a shared service. The hosted demo
        only delivers to them when the deployment enables them, and then only over HTTPS to port 443. Hostnames are
        resolved and every resolved address must be public: loopback, private, link-local, metadata, multicast and
        reserved ranges are rejected, including unusual numeric forms of IP addresses. The connection is pinned to
        the validated address (with the original name used for TLS), redirects are not followed, and timeouts and
        response sizes are bounded. If your deployment shows external endpoints as disabled, use the built-in
        receiver: every feature works with it.
      </p>
    ),
  },
  {
    id: "limits",
    title: "Guest data retention and limits",
    body: (
      <>
        <p>
          Guest workspaces are deleted, with everything in them, after 24 hours without activity. A durable cleanup job
          enforces this. You can reset or delete your workspace at any time from the Workspace page.
        </p>
        <p>
          Limits are enforced by the server and shown on the Workspace page: 5 endpoints, 256 KB payloads, hourly
          caps on events, replays and simulated events, one running simulation of up to 1,000 events and 5 minutes,
          and 4 deliveries in flight per workspace. Work beyond the in-flight limit waits in the queue; it is never
          dropped. The hosted demo runs on free infrastructure and may pause or slow down; the sidebar shows whether
          the worker is online.
        </p>
      </>
    ),
  },
  {
    id: "self-hosting",
    title: "Self-hosting",
    body: (
      <>
        <p>Requirements: Docker. Everything (PostgreSQL, API, worker, web) runs from the repository root:</p>
        <pre tabIndex={0} aria-label="Self-hosting commands">
          <code>{`cp .env.example .env     # set SECRET_KEY to a long random value
docker compose up --build
# open http://localhost:3000`}</code>
        </pre>
        <p>
          The API applies database migrations on start. Quotas are environment variables (for example{" "}
          <code>QUOTA_SIMULATION_EVENTS</code>, <code>QUOTA_WORKSPACE_CONCURRENCY</code>), so a self-hosted install can
          raise or remove the shared-demo limits. API reference: <code>/docs</code> on the API service in development.
        </p>
      </>
    ),
  },
];

export default function DocsPage() {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-8 px-4 py-10 sm:px-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <Link href="/" className="font-mono text-[13px] text-ink-2 hover:text-ink">
          webhook reliability lab
        </Link>
        <Link href="/app/simulations" className="text-[13px] text-accent hover:underline">
          Open the app →
        </Link>
      </header>
      <h1 className="text-[24px] font-semibold">Documentation</h1>
      <div className="grid gap-8 md:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Contents" className="md:sticky md:top-6 md:self-start">
          <ol className="flex flex-col gap-1 text-[13px]">
            {SECTIONS.map((s) => (
              <li key={s.id}>
                <a href={`#${s.id}`} className="text-ink-2 hover:text-ink">
                  {s.title}
                </a>
              </li>
            ))}
          </ol>
        </nav>
        <article className="docs flex min-w-0 max-w-prose flex-col gap-8 text-[14px] leading-relaxed">
          {SECTIONS.map((s) => (
            <section key={s.id} id={s.id} className="flex scroll-mt-6 flex-col gap-2">
              <h2 className="text-[16px] font-semibold">{s.title}</h2>
              {s.body}
            </section>
          ))}
        </article>
      </div>
    </div>
  );
}
