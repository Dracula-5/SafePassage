import type { NextConfig } from "next";

const dev = process.env.NODE_ENV !== "production";
// The site and API share one address. Caddy arranges that in production. Where there is no Caddy (next
// dev, Vercel), API_ORIGIN makes Next forward these paths to the API instead.
const apiOrigin = process.env.API_ORIGIN;
const API_PATHS = ["/api/:path*", "/portal/api/:path*", "/portal/whatsapp", "/health"];

// Next's own bootstrap is inline, so scripts need 'unsafe-inline'; everything else stays narrow.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "worker-src 'self' blob:",
  `connect-src 'self'${dev ? " ws:" : ""}`,
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  /* config options here */
  // Only in the Linux image (frontend.Dockerfile): on Windows this step can need symlink rights.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  async rewrites() {
    return apiOrigin ? API_PATHS.map((source) => ({ source, destination: `${apiOrigin}${source}` })) : [];
  },
  async redirects() {
    return [{ source: "/portal", destination: "/supplier", permanent: false }]; // the old address of the supplier screen
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "Content-Security-Policy", value: csp },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Permissions-Policy", value: "geolocation=(self), camera=(), microphone=()" },
        // The API already sends this; the pages need it too. Browsers ignore it over plain http, so localhost is unaffected.
        ...(dev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
      ],
    }];
  },
};

export default nextConfig;
