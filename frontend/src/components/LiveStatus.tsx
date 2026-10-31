"use client";

// The "is this live?" indicator: how long ago the last real answer came, and a bar to the next refresh.

import { useEffect, useState } from "react";

export const REFRESH_MS = 5000;

export function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const timer = setInterval(tick, everyMs);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [everyMs]);
  return now;
}

export default function LiveStatus({ updatedAt, failed }: { updatedAt: number | null; failed: boolean }) {
  const now = useNow();
  const seconds = updatedAt && now ? Math.max(0, Math.round((now - updatedAt) / 1000)) : null;
  const late = seconds !== null && seconds > 15;
  const state = failed || late ? "down" : seconds === null ? "wait" : "on";
  return (
    <span className={`live-pill ${state}`} role="status">
      <span className="live-dot" />
      {state === "down" ? "Connection lost" : state === "wait" ? "Connecting" : "Live"}
      {seconds !== null && <small>updated {seconds}s ago</small>}
      {state === "on" && <span className="live-bar" key={updatedAt} />}
    </span>
  );
}
