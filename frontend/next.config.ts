import type { NextConfig } from "next";

const dev = process.env.NODE_ENV !== "production";
const TILES = "https://tile.openstreetmap.org";
// The site and API share one address. Caddy arranges that in production. Where there is no Caddy (next
// dev, Vercel), API_ORIGIN makes Next forward these paths to the API instead.
const apiOrigin = process.env.API_ORIGIN;
const API_PATHS = ["/api/:path*", "/portal/api/:path*", "/portal/whatsapp", "/health"];

// Next's own bootstrap is inline, so scripts need 'unsafe-inline'; everything else stays narrow.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${TILES}`,
  "worker-src 'self' blob:",
  `connect-src 'self' ${TILES}${dev ? " ws:" : ""}`,
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
        // The map's tile servers expect to see which site is asking; this sends the origin and no path.
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "geolocation=(self), camera=(), microphone=()" },
      ],
    }];
  },
};

export default nextConfig;
