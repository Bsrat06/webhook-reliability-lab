export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields?: { loc: string[]; message: string }[],
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { "content-type": "application/json", "x-requested-with": "fetch", ...init.headers },
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const e = body?.error ?? {};
    throw new ApiError(res.status, e.code ?? "error", e.message ?? `Request failed (${res.status})`, e.fields);
  }
  return body as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.fields?.length) return e.fields.map((f) => `${f.loc.slice(1).join(".")}: ${f.message}`).join("; ");
    return e.message;
  }
  return "Something went wrong. Check your connection and try again.";
}
