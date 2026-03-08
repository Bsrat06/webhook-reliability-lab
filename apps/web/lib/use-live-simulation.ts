"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { fetcher } from "./api";
import type { SimulationRun, SimulationStatus, SimulationSummary } from "./types";

export type Transport = "connecting" | "live" | "reconnecting" | "polling" | "ended";

/**
 * Live view of a simulation run. Server-Sent Events push progress; the browser reconnects on its own.
 * After repeated failures it falls back to polling. Either way the API (the database) is the source of truth,
 * and a final fetch confirms the end state.
 */
export function useLiveSimulation(id: string) {
  const [transport, setTransport] = useState<Transport>("connecting");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const failures = useRef(0);
  const polling = transport === "polling";

  const swr = useSWR<SimulationRun>(`/simulations/${id}`, fetcher, {
    refreshInterval: (d) => (polling && d?.status === "running" ? 1500 : 0),
    onSuccess: () => setUpdatedAt(Date.now()),
  });
  const running = swr.data?.status === "running";
  const loaded = swr.data !== undefined;
  const { mutate } = swr;

  useEffect(() => {
    if (!running || polling || typeof EventSource === "undefined") {
      if (loaded && !running) setTransport("ended");
      else if (typeof EventSource === "undefined") setTransport("polling");
      return;
    }
    const es = new EventSource(`/api/v1/simulations/${id}/stream`);
    es.addEventListener("open", () => {
      failures.current = 0;
      setTransport("live");
    });
    es.addEventListener("progress", (e) => {
      const snap = JSON.parse((e as MessageEvent).data) as {
        status: SimulationStatus;
        end_reason: string | null;
        completed_at: string | null;
        summary: SimulationSummary;
      };
      setUpdatedAt(Date.now());
      mutate((cur) => (cur ? { ...cur, ...snap } : cur), { revalidate: false });
    });
    es.addEventListener("done", () => {
      es.close();
      setTransport("ended");
      mutate();
    });
    es.addEventListener("error", () => {
      failures.current += 1;
      if (failures.current >= 3) {
        es.close();
        setTransport("polling");
        mutate();
      } else {
        setTransport("reconnecting");
      }
    });
    return () => es.close();
  }, [id, running, polling, loaded, mutate]);

  return { ...swr, transport, updatedAt };
}
