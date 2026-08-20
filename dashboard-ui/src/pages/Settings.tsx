import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader, Select } from "../components/ui";
import { getConfig, updateConfig, getAuthStatus, createUser, getAlertsStatus, sendTestAlert, type AuthStatus, type AlertsStatus } from "../api/client";
import type { ProjectConfig, ModelEntry } from "../api/types";

const PROVIDERS = ["openai", "anthropic", "xai", "google", "azure"];

export default function Settings() {
  const [cfg, setCfg] = useState<ProjectConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [draft, setDraft] = useState({ id: "", provider: "openai", label: "" });
  const [editKey, setEditKey] = useState<string | null>(null);
  const [keyDraft, setKeyDraft] = useState("");

  const load = () => getConfig().then(setCfg).catch(() => setCfg(null));
  useEffect(() => { load(); }, []);

  async function saveKeyAlias(provider: string, envName: string) {
    setSaving(true);
    try {
      await updateConfig({ keys: { ...(cfg?.keys || {}), [provider]: envName.trim() } });
      setEditKey(null); setKeyDraft("");
      await load();
    } finally { setSaving(false); }
  }

  async function persist(models: ModelEntry[], dashboard?: Record<string, number | string | boolean>) {
    setSaving(true);
    try {
      await updateConfig({ models, ...(dashboard ? { dashboard } : {}) });
      setSavedAt(new Date().toLocaleTimeString());
      await load();
    } finally { setSaving(false); }
  }

  if (!cfg) return <div><PageHeader eyebrow="~/promptry · settings" title="Settings" description="Loading…" /></div>;

  const addModel = () => {
    if (!draft.id.trim()) return;
    persist([...cfg.models, { id: draft.id.trim(), provider: draft.provider, label: draft.label.trim() || undefined }]);
    setDraft({ id: "", provider: "openai", label: "" });
  };
  const removeModel = (id: string) => persist(cfg.models.filter((m) => m.id !== id));

  return (
    <div>
      <PageHeader eyebrow="~/promptry · settings" title="Settings"
        description={`Project config — committed to ${cfg.path}. Shared via git. API keys stay in env.`} />

      {/* API key status */}
      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Provider API keys</div>
        <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2, marginBottom: 12 }}>
          Auto-detected from environment variables — never stored in config or the database.
          Key not detected? Click it to point promptry at your variable's name.
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {PROVIDERS.map((p) => {
            const ok = cfg.key_status[p];
            const env = cfg.key_env?.[p] || p.toUpperCase() + "_API_KEY";
            const editing = editKey === p;
            return (
              <div key={p} style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-elev)" }}>
                <div
                  onClick={() => { if (!ok) { setEditKey(editing ? null : p); setKeyDraft(env); } }}
                  style={{ display: "flex", alignItems: "center", gap: 8, cursor: ok ? "default" : "pointer" }}
                  title={ok ? `Detected in ${env}` : "Click to set the env-var name"}
                >
                  <span style={{ width: 8, height: 8, borderRadius: 999, background: ok ? "var(--success)" : "var(--muted)" }} />
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>{p}</span>
                  <span className="mono" style={{ fontSize: 10.5, color: ok ? "var(--success)" : "var(--muted)" }}>{ok ? "set" : `set ${env}`}</span>
                </div>
                {editing && !ok && (
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      className="inp mono"
                      style={{ fontSize: 11, width: 190, padding: "3px 6px" }}
                      placeholder="e.g. MY_OPENAI_KEY"
                      value={keyDraft}
                      autoFocus
                      onChange={(e) => setKeyDraft(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && keyDraft.trim()) saveKeyAlias(p, keyDraft); }}
                    />
                    <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }} disabled={saving || !keyDraft.trim()} onClick={() => saveKeyAlias(p, keyDraft)}>Save</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Models */}
      <div className="card" style={{ marginBottom: 18 }}>
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Models</div>
          <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2 }}>
            The models available in the Playground and model comparison.
          </div>
        </div>
        <table className="pr">
          <thead><tr><th style={{ textAlign: "left" }}>Model id</th><th style={{ textAlign: "left" }}>Provider</th><th style={{ textAlign: "left" }}>Label</th><th></th></tr></thead>
          <tbody>
            {cfg.models.map((m) => (
              <tr key={m.id}>
                <td className="mono" style={{ fontWeight: 600 }}>{m.id}</td>
                <td style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 999, background: m.provider && cfg.key_status[m.provider] ? "var(--success)" : "var(--muted)" }} />
                  {m.provider || "—"}
                </td>
                <td style={{ color: "var(--text-dim)" }}>{m.label || "—"}</td>
                <td className="r"><span role="button" onClick={() => removeModel(m.id)} style={{ cursor: "pointer", color: "var(--muted)" }} title="remove">✕</span></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ display: "flex", gap: 8, padding: "12px 16px", borderTop: "1px solid var(--border)", alignItems: "center", flexWrap: "wrap" }}>
          <input className="inp" placeholder="model id (e.g. gpt-4o)" value={draft.id} onChange={(e) => setDraft({ ...draft, id: e.target.value })} style={{ width: 200 }} />
          <Select value={draft.provider} onChange={(v) => setDraft({ ...draft, provider: v })}
            options={PROVIDERS.map((p) => ({ value: p, label: p }))} minWidth={130} />
          <input className="inp" placeholder="label (optional)" value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} style={{ width: 160 }} />
          <button className="btn" onClick={addModel} style={{ background: "var(--accent)", color: "var(--bg)" }}>Add model</button>
          {saving && <span style={{ fontSize: 11, color: "var(--muted)" }}>saving…</span>}
          {savedAt && !saving && <span style={{ fontSize: 11, color: "var(--success)" }}>saved {savedAt}</span>}
        </div>
      </div>

      <div style={{ fontSize: 11.5, color: "var(--muted)" }}>
        Changes write to <span className="mono">{cfg.path}</span> — commit it to share with your team.
      </div>

      <TeamAccessCard />
      <AlertingCard />
    </div>
  );
}

/**
 * Alerting + incident channels (env-configured; shown read-only here with a
 * test-fire). Regression/drift/SLO/budget alerts fan out to whatever's on.
 */
function AlertingCard() {
  const [st, setSt] = useState<AlertsStatus | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getAlertsStatus().then(setSt).catch(() => setSt(null));
  }, []);

  if (!st) return null;

  const Chan = ({ on, label, hint }: { on: boolean; label: string; hint: string }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
      <span style={{ width: 8, height: 8, borderRadius: 999, background: on ? "var(--success)" : "var(--muted)" }} />
      <span style={{ color: "var(--text)" }}>{label}</span>
      <span style={{ color: "var(--muted)", fontSize: 11 }}>{on ? "configured" : hint}</span>
    </div>
  );

  async function test() {
    setBusy(true);
    setMsg(null);
    try {
      await sendTestAlert();
      setMsg("Test alert sent to all configured channels.");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "failed to send");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: 16, marginTop: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Alerting &amp; incidents</span>
        <span className="chip" style={{ fontSize: 10, background: "var(--accent-soft)", color: "var(--accent-bright)" }}>alpha</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12, lineHeight: 1.5, maxWidth: 620 }}>
        Regression, drift, SLO-breach and budget alerts fan out to whatever is configured. Set channels via env / config:{" "}
        <span className="mono">[notifications] webhook_url</span>, <span className="mono">PROMPTRY_PAGERDUTY_ROUTING_KEY</span>, or SMTP email.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <Chan on={st.webhook} label="Slack / webhook" hint="set [notifications] webhook_url" />
        <Chan on={st.pagerduty} label="PagerDuty / Opsgenie" hint="set PROMPTRY_PAGERDUTY_ROUTING_KEY" />
        <Chan on={st.email} label="Email (SMTP)" hint="set [notifications] email + SMTP" />
        <Chan on={st.otel_export} label="OpenTelemetry export" hint="call promptry.enable_otel()" />
      </div>
      <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 10 }}>
        <button className="btn" disabled={busy || !st.any} onClick={test}
          style={{ background: st.any ? "var(--accent-soft)" : undefined, border: st.any ? "1px solid var(--accent-line)" : undefined, color: st.any ? "var(--accent-bright)" : undefined }}>
          {busy ? "Sending…" : "Send test alert"}
        </button>
        {!st.any && <span style={{ fontSize: 11.5, color: "var(--muted)" }}>no channels configured yet</span>}
        {msg && <span style={{ fontSize: 12, color: "var(--secondary)" }}>{msg}</span>}
      </div>
    </div>
  );
}

/**
 * Opt-in entry point for multi-user mode. promptry defaults to a simple,
 * single-user, no-login dashboard; creating the first account switches on the
 * (alpha) team features: accounts, roles, and the audit log. Hidden complexity
 * until someone asks for it.
 */
function TeamAccessCard() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getAuthStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  if (!status) return null;
  const on = status.posture === "multiuser";

  async function enable() {
    if (!email.trim() || password.length < 8) {
      setErr("Enter an email and a password of at least 8 characters.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await createUser({ email: email.trim(), password, role: "admin" });
      window.location.reload(); // now in multi-user mode; the login gate applies
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed to enable team access");
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: 16, marginTop: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Team &amp; access</span>
        <span className="chip" style={{ fontSize: 10, background: "var(--accent-soft)", color: "var(--accent-bright)" }}>
          alpha
        </span>
      </div>

      {on ? (
        <div style={{ fontSize: 12.5, color: "var(--secondary)", marginTop: 8 }}>
          Team access is on — accounts, roles, and the audit log are active.{" "}
          <Link to="/users" style={{ color: "var(--accent-bright)" }}>Manage users →</Link>
        </div>
      ) : (
        <>
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4, marginBottom: 12, lineHeight: 1.5, maxWidth: 620 }}>
            promptry runs as a simple single-user dashboard by default. Turn on team access to add
            accounts with roles (viewer / editor / admin) and an audit log. Creating the first
            account (an admin) enables a login screen for everyone — you can’t undo this from the UI,
            so keep these credentials safe.
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input className="inp" placeholder="admin email" value={email}
              onChange={(e) => setEmail(e.target.value)} style={{ width: 200 }} />
            <input className="inp" type="password" placeholder="password (min 8)" value={password}
              onChange={(e) => setPassword(e.target.value)} style={{ width: 180 }} />
            <button className="btn" disabled={busy} onClick={enable}
              style={{ background: "var(--accent-soft)", border: "1px solid var(--accent-line)", color: "var(--accent-bright)", fontWeight: 600 }}>
              {busy ? "Enabling…" : "Enable team access"}
            </button>
          </div>
          {err && <div style={{ fontSize: 12, color: "var(--error)", marginTop: 8 }}>{err}</div>}
        </>
      )}
    </div>
  );
}
