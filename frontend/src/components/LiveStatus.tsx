"use client";

// Shared timing for the screens that refresh by themselves: the refresh interval and a clock that ticks, so
// "updated 12s ago" keeps counting between refreshes. The indicator itself is ConnectionStatus in components/ui.

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
