import type { NextConfig } from "next";

const api = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

const config: NextConfig = {
  // Standalone output is for the container image. Local Windows + pnpm builds skip it: its traced copies are
  // links into node_modules, and cleaning them on the next build deletes the real packages.
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,
  // Same-origin proxy keeps the session cookie first-party (SameSite=Lax, HttpOnly).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default config;
