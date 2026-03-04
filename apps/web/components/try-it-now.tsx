"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { errorText, post } from "@/lib/api";
import { Button, ErrorNote } from "./ui";

export function TryItNow() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="primary"
        className="h-10 px-5 text-[14px]"
        busy={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await post("/guest/session");
            router.push("/app/events");
          } catch (e) {
            setError(errorText(e));
            setBusy(false);
          }
        }}
      >
        Try it now
      </Button>
      <ErrorNote>{error}</ErrorNote>
    </div>
  );
}
