"use client";

// Which supplier screen is open. It lives in the address (#/orders, #/orders/<id>, ...) so the browser's back
// button works and a screen can be bookmarked, without adding routes the rest of the app would have to know.

import { useSyncExternalStore } from "react";

export type View =
  | { name: "home" }
  | { name: "orders" }
  | { name: "order"; id: string }
  | { name: "inventory" }
  | { name: "report" }
  | { name: "profile" };

export function parseView(hash: string): View {
  const [name, id] = hash.replace(/^#\/?/, "").split("/");
  if (name === "orders" && id) return { name: "order", id: decodeURIComponent(id) };
  if (name === "orders" || name === "inventory" || name === "report" || name === "profile") return { name };
  return { name: "home" };
}

export function viewHash(view: View): string {
  if (view.name === "home") return "#/";
  if (view.name === "order") return `#/orders/${encodeURIComponent(view.id)}`;
  return `#/${view.name}`;
}

// How many screens this visit has opened itself, so Back only steps through history it made.
let opened = 0;

function subscribe(onChange: () => void): () => void {
  const onPop = () => {
    opened = Math.max(0, opened - 1);
    onChange();
  };
  window.addEventListener("hashchange", onChange);
  window.addEventListener("popstate", onPop);
  return () => {
    window.removeEventListener("hashchange", onChange);
    window.removeEventListener("popstate", onPop);
  };
}

export function useHash(): string {
  return useSyncExternalStore(subscribe, () => window.location.hash, () => "");
}

export function go(view: View): void {
  const next = viewHash(view);
  if (window.location.hash === next) return;
  opened += 1;
  window.location.hash = next;
}

/** Back to where the person came from, or to `fallback` when this screen was opened directly. */
export function back(fallback: View): void {
  if (opened > 0) window.history.back();
  else window.location.replace(viewHash(fallback));
}

function subscribeMedia(query: string) {
  return (onChange: () => void) => {
    const list = window.matchMedia(query);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  };
}

/** True when the screen matches the media query. Used to pick a table or cards, so only one is ever in the page. */
export function useMedia(query: string): boolean {
  return useSyncExternalStore(subscribeMedia(query), () => window.matchMedia(query).matches, () => false);
}

export const DESKTOP = "(min-width: 1024px)";
