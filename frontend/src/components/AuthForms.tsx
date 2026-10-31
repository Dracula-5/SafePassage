"use client";

// Sign-in, sign-up and password reset, on one shared layout whose side panel shows the live picture.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, ReactNode, useEffect, useState } from "react";

import { api, authApi, PublicStatus, Role } from "@/lib/api";
import { HOME } from "@/lib/session";

import { Logo } from "./AppShell";

export function usePublicStatus(): PublicStatus | null {
  const [status, setStatus] = useState<PublicStatus | null>(null);
  useEffect(() => {
    const load = () => api.status().then(setStatus, () => undefined);
    const first = setTimeout(load, 0);
    const timer = setInterval(load, 5000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, []);
  return status;
}

function AuthLayout({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  const status = usePublicStatus();
  return (
    <div className="auth">
      <aside className="auth-side">
        <Link href="/" className="brand"><Logo size={34} /><span>ReliefRoute</span></Link>
        <h2>Relief goods, on roads that are actually safe.</h2>
        <p>Live government alerts, real stock and real roads, with a person approving every dispatch.</p>
        {status && (
          <dl className="auth-stats">
            <div><dt>Active alerts</dt><dd>{status.hazards_active}</dd></div>
            <div><dt>Severe or worse</dt><dd>{status.hazards_severe}</dd></div>
            <div><dt>Live sources</dt><dd>{status.sources_fresh}/{status.sources_total}</dd></div>
          </dl>
        )}
      </aside>
      <main className="auth-main">
        <div className="auth-card">
          <h1>{title}</h1>
          <p className="muted">{subtitle}</p>
          {children}
        </div>
      </main>
    </div>
  );
}

function useSubmit(action: () => Promise<void>) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      const message = (err as Error).message;
      setError(message.includes("fetch") ? "Could not reach the server. Check your connection." : message);
      setBusy(false);
    }
  }
  return { error, busy, submit };
}

export function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { error, busy, submit } = useSubmit(async () => {
    const account = await authApi.signIn(email, password);
    const next = params.get("next");
    router.replace(next?.startsWith("/") && !next.startsWith("//") ? next : HOME[account.role]);
  });
  return (
    <AuthLayout title="Welcome back" subtitle="Sign in with your email.">
      <form className="form" onSubmit={submit}>
        <label>Email<input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <div className="alert danger" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
      <p className="auth-foot">New here? <Link href="/signup">Create an account</Link></p>
      <p className="auth-foot small muted">Forgot your password? Ask a duty officer for a reset link.</p>
    </AuthLayout>
  );
}

const ROLES: { role: Role; title: string; text: string; code?: string }[] = [
  { role: "operator", title: "Duty officer", text: "Watch hazards and approve dispatches", code: "Operator code" },
  { role: "driver", title: "Driver", text: "Get routes and share your GPS", code: "Enrolment code" },
  { role: "supplier", title: "Supplier", text: "Report the stock you hold" },
];

export function SignUpForm() {
  const router = useRouter();
  const params = useSearchParams();
  const asked = params.get("role") as Role | null;
  const [role, setRole] = useState<Role>(ROLES.some((r) => r.role === asked) ? asked! : "supplier");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const chosen = ROLES.find((r) => r.role === role)!;
  const { error, busy, submit } = useSubmit(async () => {
    await authApi.signUp({ email, password, name, role, code: chosen.code ? code : undefined });
    router.replace(HOME[role]);
  });
  return (
    <AuthLayout title="Create your account" subtitle="Choose how you will use ReliefRoute.">
      <form className="form" onSubmit={submit}>
        <div className="role-picker" role="radiogroup" aria-label="Account type">
          {ROLES.map((r) => (
            <button type="button" key={r.role} role="radio" aria-checked={role === r.role}
              className={role === r.role ? "role selected" : "role"} onClick={() => setRole(r.role)}>
              <b>{r.title}</b>
              <small>{r.text}</small>
            </button>
          ))}
        </div>
        <label>Full name<input autoComplete="name" required minLength={2} maxLength={100} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>Email<input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Password
          <input type="password" autoComplete="new-password" required minLength={10} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} />
          <small className="muted">At least 10 characters.</small>
        </label>
        {chosen.code && (
          <label>{chosen.code}
            <input autoComplete="off" required value={code} onChange={(e) => setCode(e.target.value)} />
            <small className="muted">Your dispatcher gives you this. It keeps strangers out of the fleet.</small>
          </label>
        )}
        {role === "supplier" && <p className="small muted">New suppliers start unverified. A duty officer checks any plan that uses your stock.</p>}
        {error && <div className="alert danger" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? "Creating your account…" : "Create account"}</button>
      </form>
      <p className="auth-foot">Already have an account? <Link href="/signin">Sign in</Link></p>
    </AuthLayout>
  );
}

export function ResetForm() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const { error, busy, submit } = useSubmit(async () => {
    await authApi.reset(token, password);
    setDone(true);
  });
  return (
    <AuthLayout title="Choose a new password" subtitle="This link works once.">
      {done ? (
        <>
          <div className="alert ok" role="status">Password changed. Other devices have been signed out.</div>
          <p className="auth-foot"><Link href="/signin">Sign in</Link></p>
        </>
      ) : !token ? (
        <div className="alert danger">This link is not complete. Ask a duty officer for a new one.</div>
      ) : (
        <form className="form" onSubmit={submit}>
          <label>New password
            <input type="password" autoComplete="new-password" required minLength={10} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} />
            <small className="muted">At least 10 characters.</small>
          </label>
          {error && <div className="alert danger" role="alert">{error}</div>}
          <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save password"}</button>
        </form>
      )}
    </AuthLayout>
  );
}
