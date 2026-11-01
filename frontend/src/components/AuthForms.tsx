"use client";

// Sign-in, sign-up and password reset, on one shared centred card in the same light style.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";

import { api, ApiError, authApi, PublicStatus, Role } from "@/lib/api";
import { HOME } from "@/lib/session";

import { Logo } from "./AppShell";
import styles from "./signup.module.css";
import ReliefIllustration from "./ui/ReliefIllustration";

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
  return (
    <div className={styles.page}>
      <main className={styles.card}>
        <Link href="/" className={styles.brand} aria-label="SafePassage home"><Logo size={30} /><span>SafePassage</span></Link>
        <div className={styles.heading}>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        {children}
      </main>
    </div>
  );
}

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** Runs one submit at a time. Rate-limited forms honor Retry-After; local signup does not retain a stale wait. */
function useSubmit(action: () => Promise<void>, { rateLimitCountdown = true }: { rateLimitCountdown?: boolean } = {}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retryAt, setRetryAt] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const inFlight = useRef(false);

  useEffect(() => {
    if (retryAt === null) return;
    const tick = () => {
      const current = Date.now();
      setNow(current);
      if (current >= retryAt) setRetryAt(null);
    };
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [retryAt]);

  const waitSeconds = rateLimitCountdown && retryAt !== null && now < retryAt ? Math.ceil((retryAt - now) / 1000) : 0;
  const waitText = waitSeconds > 0 ? clock(waitSeconds) : null;
  const visibleError = !rateLimitCountdown && error?.startsWith("Too many attempts. Wait ") ? null : error;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (inFlight.current || waitText) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      if (err instanceof ApiError && err.status === 429 && err.retryAfter && rateLimitCountdown) {
        // Only a wait the server named: a 429 without Retry-After is shown as it is, with no invented countdown.
        setNow(Date.now());
        setRetryAt(Date.now() + err.retryAfter * 1000);
        setError(`Too many attempts. Wait ${clock(err.retryAfter!)} before trying again.`);
      } else if (err instanceof ApiError && err.status === 429 && !rateLimitCountdown) {
        setRetryAt(null);
        setError(err.message);
      } else {
        const message = (err as Error).message;
        setError(message.includes("fetch") ? "Could not reach the server. Check your connection." : message);
      }
      setBusy(false);
    } finally {
      inFlight.current = false;
    }
  }
  return { error: visibleError, busy, submit, waitText };
}

export function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { error, busy, submit, waitText } = useSubmit(async () => {
    const account = await authApi.signIn(email, password);
    const next = params.get("next");
    router.replace(next?.startsWith("/") && !next.startsWith("//") ? next : HOME[account.role]);
  });
  return (
    <AuthLayout title="Welcome back" subtitle="Sign in with your email.">
      <form className={styles.form} onSubmit={submit}>
        <label className={styles.field}>Email
          <input className={styles.control} type="email" autoComplete="email" required placeholder="you@example.org" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <PasswordField id="signin-password" label="Password" autoComplete="current-password" value={password} onChange={setPassword} />
        {error && <div className="alert danger" role="alert">{error}</div>}
        <button className={styles.submit} disabled={busy || waitText !== null}>{waitText ? `Try again in ${waitText}` : busy ? "Signing in…" : "Sign in"}</button>
      </form>
      <p className={styles.foot}>New here? <Link href="/signup">Create an account</Link></p>
      <p className={styles.foot} style={{ marginTop: -8 }}><Link href="/forgot">Forgot your password?</Link></p>
    </AuthLayout>
  );
}

const ROLES: { role: Role; title: string; text: string; icon: ReactNode }[] = [
  {
    role: "operator", title: "Duty Officer", text: "Approve dispatches",
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 3 4.5 6v5.5c0 4.7 3 8 7.5 9.5 4.5-1.5 7.5-4.8 7.5-9.5V6z" /><path d="m8.8 12 2.4 2.4 4-4.6" />
      </svg>
    ),
  },
  {
    role: "driver", title: "Driver", text: "Get routes, share GPS",
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M2.5 6.5h11v10h-11z" /><path d="M13.5 10h4l3 3v3.5h-7" /><circle cx="7" cy="17.5" r="1.8" /><circle cx="17" cy="17.5" r="1.8" />
      </svg>
    ),
  },
  {
    role: "supplier", title: "Supplier", text: "Report stock you hold",
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M3.5 7.5 12 3l8.5 4.5v9L12 21l-8.5-4.5z" /><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9" />
      </svg>
    ),
  },
];

function PasswordField({ id, label, value, onChange, autoComplete, hint }: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "new-password" | "current-password";
  /** A rule worth showing, such as the minimum length. A sign-in field has none: it accepts what was set. */
  hint?: string;
}) {
  const [shown, setShown] = useState(false);
  const choosing = autoComplete === "new-password";
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <span className={styles.passwordWrap}>
        <input id={id} className={styles.control} type={shown ? "text" : "password"} autoComplete={autoComplete} required
          {...(choosing ? { minLength: 10, maxLength: 128 } : {})} value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={hint ? `${id}-hint` : undefined} />
        <button type="button" className={styles.reveal} aria-pressed={shown} aria-label={shown ? "Hide characters" : "Show characters"}
          onClick={() => setShown((v) => !v)}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="3" />
            {shown && <path d="M4 4l16 16" />}
          </svg>
        </button>
      </span>
      {hint && <span id={`${id}-hint`} className={styles.hint}>{hint}</span>}
    </div>
  );
}

export function SignUpForm() {
  const router = useRouter();
  const params = useSearchParams();
  const asked = params.get("role") as Role | null;
  const [role, setRole] = useState<Role>(ROLES.some((r) => r.role === asked) ? asked! : "supplier");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState<Role | null>(null);
  // Only where the server enforces approval. In local development every account is usable at once.
  const [approvalOn, setApprovalOn] = useState(false);
  useEffect(() => {
    authApi.options().then((o) => setApprovalOn(o.approval), () => setApprovalOn(false));
  }, []);
  const localDevelopment = typeof window !== "undefined"
    && ["localhost", "127.0.0.1", "::1", "[::1]"].includes(window.location.hostname);
  const { error, busy, submit, waitText } = useSubmit(async () => {
    const account = await authApi.signUp({ email, password, name, role });
    if (!account.approved) {
      setPending(role);
      return;
    }
    router.replace(HOME[role]);
  }, { rateLimitCountdown: !localDevelopment });
  const needsApproval = approvalOn && (role === "operator" || role === "driver");

  return (
    <div className={styles.page}>
      <main className={styles.card}>
        <Link href="/" className={styles.brand} aria-label="SafePassage home">
          <Logo size={30} /><span>SafePassage</span>
        </Link>
        <ReliefIllustration className={styles.illustration} />
        <div className={styles.heading}>
          <h1>Create your account</h1>
          <p>Choose how you will use SafePassage.</p>
        </div>

        {pending ? (
          <div className={styles.done} role="status">
            Your {pending === "operator" ? "duty officer" : "driver"} account is pending approval by a duty officer.
            You can sign in once it has been approved.
            <Link className={styles.foot} href="/signin">Back to sign in</Link>
          </div>
        ) : (
          <form className={styles.form} onSubmit={submit}>
            <div className={styles.roles} role="radiogroup" aria-label="Account type">
              {ROLES.map((r) => (
                <button type="button" key={r.role} role="radio" aria-checked={role === r.role} className={styles.role} onClick={() => setRole(r.role)}>
                  <span className={styles.roleIcon}>{r.icon}</span>
                  <span>{r.title}</span>
                  <small>{r.text}</small>
                </button>
              ))}
            </div>

            <label className={styles.field}>Full Name
              <input className={styles.control} autoComplete="name" required minLength={2} maxLength={100} placeholder="Your name"
                value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className={styles.field}>Email
              <input className={styles.control} type="email" autoComplete="email" required placeholder="you@example.org"
                value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <PasswordField id="signup-password" label="Password" autoComplete="new-password" hint="At least 10 characters." value={password} onChange={setPassword} />

            {needsApproval ? (
              <div className={styles.approval} role="note">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 3 4.5 6v5.5c0 4.7 3 8 7.5 9.5 4.5-1.5 7.5-4.8 7.5-9.5V6z" /><path d="m8.8 12 2.4 2.4 4-4.6" />
                </svg>
                <span>
                  <b>Account approval</b>
                  A duty officer reviews duty officer and driver accounts before they can sign in. No code is needed.
                </span>
              </div>
            ) : role === "supplier" ? (
              <p className={styles.note}>Supplier accounts can start right away. A duty officer verifies your organisation before any plan uses your stock.</p>
            ) : null}

            {error && <div className={styles.error} role="alert">{error}</div>}
            <button className={styles.submit} disabled={busy || waitText !== null}>{waitText ? `Try again in ${waitText}` : busy ? "Creating your account…" : "Create account"}</button>
          </form>
        )}

        {!pending && <p className={styles.foot}>Already have an account? <Link href="/signin">Sign in</Link></p>}
      </main>
    </div>
  );
}

export function ForgotForm() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [mailOn, setMailOn] = useState<boolean | null>(null);
  useEffect(() => {
    authApi.options().then((o) => setMailOn(o.email), () => setMailOn(false));
  }, []);
  const { error, busy, submit } = useSubmit(async () => {
    await authApi.forgot(email);
    setSent(true);
  });
  return (
    <AuthLayout title="Forgot your password?" subtitle={mailOn === false ? "A duty officer can help." : "We will email you a link to choose a new one."}>
      {mailOn === false ? (
        <div className="alert" role="status">Ask a duty officer for a reset link. They can make one in Team &amp; Access.</div>
      ) : sent ? (
        <div className="alert ok" role="status">If that address has an account, a link is on its way. Check your spam folder too.</div>
      ) : (
        <form className={styles.form} onSubmit={submit}>
          <label className={styles.field}>Email
            <input className={styles.control} type="email" autoComplete="email" required placeholder="you@example.org" value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          {error && <div className="alert danger" role="alert">{error}</div>}
          <button className={styles.submit} disabled={busy || mailOn === null}>{busy ? "Sending…" : "Send reset link"}</button>
        </form>
      )}
      <p className={styles.foot}><Link href="/signin">Back to sign in</Link></p>
    </AuthLayout>
  );
}

export function VerifyEmail() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<"checking" | "done" | "failed">("checking");
  useEffect(() => {
    authApi.verify(token).then(() => setState("done"), () => setState("failed"));
  }, [token]);
  return (
    <AuthLayout title="Confirm your email" subtitle="Thank you for checking.">
      {state === "checking" && <p className="muted" style={{ textAlign: "center" }}>Checking your link…</p>}
      {state === "done" && <div className="alert ok" role="status">Your email is confirmed.</div>}
      {state === "failed" && <div className="alert danger" role="alert">This link has expired or was already used.</div>}
      <p className={styles.foot}><Link href="/">Continue to SafePassage</Link></p>
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
          <p className={styles.foot}><Link href="/signin">Sign in</Link></p>
        </>
      ) : !token ? (
        <div className="alert danger">This link is not complete. Ask a duty officer for a new one.</div>
      ) : (
        <form className={styles.form} onSubmit={submit}>
          <PasswordField id="reset-password" label="New password" autoComplete="new-password" hint="At least 10 characters." value={password} onChange={setPassword} />
          {error && <div className="alert danger" role="alert">{error}</div>}
          <button className={styles.submit} disabled={busy}>{busy ? "Saving…" : "Save password"}</button>
        </form>
      )}
    </AuthLayout>
  );
}
