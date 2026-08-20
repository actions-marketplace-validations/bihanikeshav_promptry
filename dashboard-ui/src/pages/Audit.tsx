import { useEffect, useState } from "react";
import { PageHeader, Select } from "../components/ui";
import { listAudit, type AuditEntry } from "../api/client";

const PAGE = 50;

const ACTIONS = [
  { value: "", label: "all actions" },
  { value: "auth.login", label: "auth.login" },
  { value: "auth.logout", label: "auth.logout" },
  { value: "auth.oidc", label: "auth.oidc" },
  { value: "user.create", label: "user.create" },
  { value: "user.update", label: "user.update" },
  { value: "user.delete", label: "user.delete" },
  { value: "prompt.update", label: "prompt.update" },
  { value: "prompt.promote", label: "prompt.promote" },
  { value: "prompt.prune", label: "prompt.prune" },
  { value: "suite.create", label: "suite.create" },
  { value: "budget.create", label: "budget.create" },
  { value: "config.update", label: "config.update" },
  { value: "retention.run", label: "retention.run" },
];

export default function Audit() {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    listAudit({ limit: PAGE, offset, action: action || undefined })
      .then((r) => {
        setRows(r.entries);
        setTotal(r.total);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : "failed to load audit log"));
  }, [offset, action]);

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · admin"
        title="Audit log"
        description="Append-only record of every sign-in and state change — who did what, when, and from where."
        actions={
          <Select
            value={action}
            onChange={(v) => {
              setOffset(0);
              setAction(v);
            }}
            options={ACTIONS}
            minWidth={160}
            searchable
          />
        }
      />

      {err && (
        <div className="card" style={{ padding: "10px 14px", marginBottom: 16, background: "var(--error-soft)", color: "var(--error)", fontSize: 12.5 }}>
          {err}
        </div>
      )}

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--muted)", fontSize: 11 }}>
              {["Time", "Actor", "Action", "Target", "Result", "IP", "Detail"].map((h) => (
                <th key={h} style={{ padding: "10px 14px", fontWeight: 500, borderBottom: "1px solid var(--border)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} style={{ borderBottom: "1px solid var(--border)", verticalAlign: "top" }}>
                <td style={{ padding: "8px 14px", color: "var(--muted)", whiteSpace: "nowrap" }}>
                  {new Date(r.ts + "Z").toLocaleString()}
                </td>
                <td style={{ padding: "8px 14px" }} className="mono">{r.actor || "—"}</td>
                <td style={{ padding: "8px 14px" }} className="mono">{r.action}</td>
                <td style={{ padding: "8px 14px", color: "var(--secondary)" }} className="mono">
                  {r.target || "—"}
                </td>
                <td style={{ padding: "8px 14px" }}>
                  <span style={{ color: r.result === "ok" ? "var(--success)" : "var(--error)" }}>
                    {r.result}
                  </span>
                </td>
                <td style={{ padding: "8px 14px", color: "var(--muted)" }} className="mono">{r.ip || "—"}</td>
                <td style={{ padding: "8px 14px", color: "var(--muted)", maxWidth: 260 }}>
                  {r.detail ? (
                    <span className="mono" style={{ fontSize: 11 }}>{JSON.stringify(r.detail)}</span>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} style={{ padding: 20, textAlign: "center", color: "var(--muted)" }}>
                  No audit entries{action ? ` for ${action}` : ""} yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12, fontSize: 12, color: "var(--muted)" }}>
        <button
          className="btn"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE))}
          style={{ fontSize: 11 }}
        >
          ← newer
        </button>
        <span>
          {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE, total)} of {total}
        </span>
        <button
          className="btn"
          disabled={offset + PAGE >= total}
          onClick={() => setOffset(offset + PAGE)}
          style={{ fontSize: 11 }}
        >
          older →
        </button>
      </div>
    </div>
  );
}
