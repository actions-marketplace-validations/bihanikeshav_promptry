import { useEffect, useState } from "react";
import { PageHeader, Select } from "../components/ui";
import { getConfig, updateConfig } from "../api/client";
import type { ProjectConfig, ModelEntry } from "../api/types";

const PROVIDERS = ["openai", "anthropic", "xai", "google", "azure"];

export default function Settings() {
  const [cfg, setCfg] = useState<ProjectConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [draft, setDraft] = useState({ id: "", provider: "openai", label: "" });

  const load = () => getConfig().then(setCfg).catch(() => setCfg(null));
  useEffect(() => { load(); }, []);

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
          Detected from environment variables — never stored in config or the database.
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {PROVIDERS.map((p) => {
            const ok = cfg.key_status[p];
            const env = { openai: "OPENAI_API_KEY", anthropic: "ANTHROPIC_API_KEY", xai: "XAI_API_KEY", google: "GEMINI_API_KEY", azure: "AZURE_OPENAI_API_KEY" }[p];
            return (
              <div key={p} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-elev)" }}>
                <span style={{ width: 8, height: 8, borderRadius: 999, background: ok ? "var(--success)" : "var(--muted)" }} />
                <span style={{ fontSize: 12.5, fontWeight: 600 }}>{p}</span>
                <span className="mono" style={{ fontSize: 10.5, color: ok ? "var(--success)" : "var(--muted)" }}>{ok ? "set" : `set ${env}`}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Models */}
      <div className="card" style={{ overflow: "hidden", marginBottom: 18 }}>
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
    </div>
  );
}
