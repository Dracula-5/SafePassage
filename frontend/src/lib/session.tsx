"use client";

// Who is signed in. Each signed-in screen calls useAccount with the role it is for.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Account, ApiError, authApi, Role } from "./api";

export const HOME: Record<Role, string> = { operator: "/dashboard", driver: "/driver", supplier: "/supplier" };
const REMEMBERED = "rr_account";

function remembered(): Account | null {
  try {
    return JSON.parse(localStorage.getItem(REMEMBERED) ?? "null");
  } catch {
    return null;
  }
}

function remember(account: Account | null): void {
  try {
    if (account) localStorage.setItem(REMEMBERED, JSON.stringify(account));
    else localStorage.removeItem(REMEMBERED);
  } catch {
    // Storage is blocked: the only loss is opening the driver screen without signal.
  }
}

/** Sends people to sign-in, or to their own screen, and returns the account once it is the right one.
 *  With no connection it trusts the last account seen, so a driver can open the app in a dead zone. */
export function useAccount(role: Role): Account | null {
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);

  useEffect(() => {
    let cancelled = false;
    authApi.me().then(
      (me) => {
        remember(me);
        if (cancelled) return;
        if (me.role === role) setAccount(me);
        else router.replace(HOME[me.role]);
      },
      (err) => {
        if (cancelled) return;
        const offline = remembered();
        if (err instanceof ApiError) router.replace(`/signin?next=${encodeURIComponent(window.location.pathname)}`);
        else if (offline?.role === role) setAccount(offline);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [role, router]);

  return account;
}

export async function signOut(): Promise<void> {
  remember(null);
  await authApi.signOut().catch(() => undefined);
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full reload, so nothing from this session stays in memory
  window.location.href = "/signin";
}
