import type { NextConfig } from "next";

const dev = process.env.NODE_ENV !== "production";
const api = process.env.NEXT_PUBLIC_API_BASE ?? "";
const TILES = "https://tile.openstreetmap.org";

// Next's own bootstrap is inline, so scripts need 'unsafe-inline'; everything else stays narrow. The
// dashboard holds the operator token, so where it can talk to and who can frame it both matter.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${TILES}`,
  "worker-src 'self' blob:",
  `connect-src 'self' ${api} ${TILES}${dev ? " ws:" : ""}`,
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "Content-Security-Policy", value: csp },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Permissions-Policy", value: "geolocation=(), camera=(), microphone=()" },
      ],
    }];
  },
};

export default nextConfig;
