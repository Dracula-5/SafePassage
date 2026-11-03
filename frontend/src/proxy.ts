// Marks every call this site forwards to the API (next.config.ts rewrites it to API_ORIGIN) as one a program makes. A free
// ngrok tunnel, which the laptop demo uses, would otherwise answer a call carrying a browser's User-Agent with its
// warning page instead of the API's JSON. Any other host ignores the header.

import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  const headers = new Headers(request.headers);
  headers.set("ngrok-skip-browser-warning", "1");
  return NextResponse.next({ request: { headers } });
}

// The same paths as API_PATHS in next.config.ts: pages and static files never pass through here.
export const config = { matcher: ["/api/:path*", "/portal/api/:path*", "/portal/whatsapp", "/health"] };
