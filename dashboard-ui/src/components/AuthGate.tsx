import {
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  AUTH_REQUIRED_EVENT,
  getAuthStatus,
  login,
  loginUser,
  type AuthStatus,
} from "../api/client";

/**
 * Optional auth shell. When the backend has PROMPTRY_AUTH_TOKEN set and the
 * browser has no valid session cookie, render a login form instead of the app.
 * When auth is disabled (local default), children render immediately.
 */
const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "var(--bg-elev)",
  border: "1px solid var(--border-strong)",
  borderRadius: "var(--r-md)",
  padding: "10px 12px",
  color: "var(--text)",
  fontSize: 13,
  outline: "none",
  marginBottom: 12,
};

export default function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    return getAuthStatus()
      .then(setStatus)
      .catch(() => setStatus({ required: false, authenticated: true }));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const onNeed = () => {
      setStatus((s) => (s ? { ...s, authenticated: false, required: true } : { required: true, authenticated: false }));
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, onNeed);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onNeed);
  }, []);

  const multiuser = status?.posture === "multiuser";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (multiuser) {
        await loginUser(email.trim(), password);
      } else {
        await login(token.trim());
      }
      // Re-read status so posture/role/email reflect the new session.
      await refresh();
      setToken("");
      setPassword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  if (status == null) {
    return (
      <div className="pr-grid-bg" style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
        <div style={{ color: "var(--muted)", fontSize: 13 }}>checking session…</div>
      </div>
    );
  }

  if (status.required && !status.authenticated) {
    return (
      <div className="pr-grid-bg" style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24 }}>
        <form
          onSubmit={onSubmit}
          style={{
            width: "100%",
            maxWidth: 400,
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--r-xl)",
            boxShadow: "var(--shadow-md)",
            padding: "28px 28px 24px",
          }}
        >
          <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--secondary)", marginBottom: 8 }}>
            promptry
          </div>
          <h1 style={{ margin: "0 0 6px", fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em" }}>
            Sign in
          </h1>
          <p style={{ margin: "0 0 20px", fontSize: 13, color: "var(--secondary)", lineHeight: 1.45 }}>
            {multiuser
              ? "Sign in with your promptry account."
              : "This dashboard is locked. Enter the shared secret from PROMPTRY_AUTH_TOKEN."}
          </p>

          {multiuser ? (
            <>
              <label style={{ display: "block", fontSize: 12, color: "var(--secondary)", marginBottom: 6 }}>
                Email
              </label>
              <input
                type="email"
                autoFocus
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                style={inputStyle}
              />
              <label style={{ display: "block", fontSize: 12, color: "var(--secondary)", marginBottom: 6 }}>
                Password
              </label>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                style={inputStyle}
              />
            </>
          ) : (
            <>
              <label style={{ display: "block", fontSize: 12, color: "var(--secondary)", marginBottom: 6 }}>
                Access token
              </label>
              <input
                type="password"
                autoFocus
                autoComplete="current-password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="paste token…"
                style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
              />
            </>
          )}

          {error && (
            <div
              style={{
                marginBottom: 12,
                padding: "8px 10px",
                borderRadius: "var(--r-md)",
                background: "var(--error-soft)",
                color: "var(--error)",
                fontSize: 12.5,
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={busy || (multiuser ? !email.trim() || !password : !token.trim())}
            className="btn"
            style={{
              width: "100%",
              justifyContent: "center",
              padding: "10px 14px",
              background: "var(--accent-soft)",
              border: "1px solid var(--accent-line)",
              color: "var(--accent-bright)",
              fontWeight: 600,
              opacity: busy || (multiuser ? !email.trim() || !password : !token.trim()) ? 0.55 : 1,
              cursor: busy ? "not-allowed" : "pointer",
            }}
          >
            {busy ? "Signing in…" : multiuser ? "Sign in" : "Unlock dashboard"}
          </button>

          <p style={{ margin: "16px 0 0", fontSize: 11.5, color: "var(--muted)", lineHeight: 1.45 }}>
            {multiuser
              ? "Contact an admin if you don't have an account yet."
              : "Machine clients can send Authorization: Bearer $PROMPTRY_AUTH_TOKEN instead of logging in."}
          </p>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
