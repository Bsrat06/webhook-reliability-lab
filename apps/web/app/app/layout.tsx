"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import useSWR from "swr";
import { ApiError, fetcher } from "@/lib/api";
import type { Session } from "@/lib/types";
import { Mono, relTime, shortId } from "@/components/ui";

const NAV = [
  { href: "/app/simulations", label: "Simulations" },
  { href: "/app/events", label: "Events" },
  { href: "/app/dead-letters", label: "Dead letters" },
  { href: "/app/endpoints", label: "Endpoints" },
  { href: "/app/workspace", label: "Workspace" },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { data: session, error } = useSWR<Session>("/session", fetcher, { refreshInterval: 10_000 });

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) router.replace("/?session=expired");
  }, [error, router]);

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-4 border-b border-rule bg-panel px-4 py-3 md:w-52 md:border-r md:border-b-0 md:py-5">
        <Link href="/" className="font-mono text-[13px] text-ink-2 hover:text-ink">
          webhook reliability lab
        </Link>
        <nav aria-label="Workspace" className="flex gap-1 overflow-x-auto md:flex-col">
          {NAV.map((n) => {
            const active = path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-sm px-2 py-1.5 text-[13px] whitespace-nowrap ${
                  active ? "bg-paper font-semibold text-ink" : "text-ink-2 hover:text-ink"
                }`}
              >
                {n.label}
                {n.href === "/app/dead-letters" && session?.usage.dead_letters_open ? (
                  <span className="ml-1.5 rounded-sm bg-bad px-1 font-mono text-[11px] text-accent-ink" aria-label={`${session.usage.dead_letters_open} open`}>
                    {session.usage.dead_letters_open}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto hidden flex-col gap-2 text-[12px] text-ink-3 md:flex">
          {session ? (
            <>
              <WorkerStatus session={session} />
              <span>
                Guest <Mono>{shortId(session.workspace.id)}</Mono>
              </span>
            </>
          ) : null}
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-5 sm:px-6">
        {session && <div className="mb-3 md:hidden"><WorkerStatus session={session} /></div>}
        {children}
      </main>
    </div>
  );
}

function WorkerStatus({ session }: { session: Session }) {
  const w = session.worker;
  const delayed = w.oldest_due_seconds > 10;
  const label = !w.online ? "Worker offline: deliveries are paused" : delayed ? `Queue delayed ${w.oldest_due_seconds}s` : "Worker online";
  const cls = !w.online ? "text-bad" : delayed ? "text-warn" : "text-ok";
  return (
    <span className={`text-[12px] ${cls}`} title={w.last_heartbeat_at ? `Last heartbeat ${relTime(w.last_heartbeat_at)}` : "No heartbeat yet"}>
      <span aria-hidden>{w.online ? "●" : "○"}</span> {label}
    </span>
  );
}
