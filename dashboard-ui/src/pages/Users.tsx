import { useEffect, useState } from "react";
import { PageHeader, Select } from "../components/ui";
import {
  listUsers,
  createUser,
  updateUser,
  deleteUser,
  setUserPassword,
  getMe,
  type User,
  type Me,
} from "../api/client";

const ROLE_OPTS = [
  { value: "viewer", label: "viewer" },
  { value: "editor", label: "editor" },
  { value: "admin", label: "admin" },
];

const ROLE_COLOR: Record<string, string> = {
  admin: "var(--error)",
  editor: "var(--accent-bright)",
  viewer: "var(--secondary)",
};

export default function Users() {
  const [users, setUsers] = useState<User[] | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({ email: "", name: "", password: "", role: "viewer" });
  const [pwFor, setPwFor] = useState<number | null>(null);
  const [pwDraft, setPwDraft] = useState("");

  const load = () =>
    listUsers()
      .then((r) => setUsers(r.users))
      .catch((e) => setErr(e instanceof Error ? e.message : "failed to load users"));

  useEffect(() => {
    load();
    getMe().then(setMe).catch(() => setMe(null));
  }, []);

  async function guarded(fn: () => Promise<unknown>) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "action failed");
    } finally {
      setBusy(false);
    }
  }

  const add = () => {
    if (!draft.email.trim() || draft.password.length < 8) {
      setErr("Email and a password of at least 8 characters are required.");
      return;
    }
    guarded(async () => {
      await createUser({
        email: draft.email.trim(),
        name: draft.name.trim() || undefined,
        password: draft.password,
        role: draft.role,
      });
      setDraft({ email: "", name: "", password: "", role: "viewer" });
    });
  };

  const resetPw = (id: number) => {
    if (pwDraft.length < 8) {
      setErr("Password must be at least 8 characters.");
      return;
    }
    guarded(async () => {
      await setUserPassword(id, pwDraft);
      setPwFor(null);
      setPwDraft("");
    });
  };

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · admin"
        title="Users"
        description="Local accounts and their roles. viewer reads · editor edits prompts & suites · admin manages users, config, budgets, and prod promotion."
      />

      {err && (
        <div
          className="card"
          style={{ padding: "10px 14px", marginBottom: 16, background: "var(--error-soft)", color: "var(--error)", fontSize: 12.5 }}
        >
          {err}
        </div>
      )}

      {/* Add user */}
      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>Add a user</div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <input
            placeholder="email"
            value={draft.email}
            onChange={(e) => setDraft({ ...draft, email: e.target.value })}
            style={field(200)}
          />
          <input
            placeholder="name (optional)"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            style={field(160)}
          />
          <input
            type="password"
            placeholder="password (min 8)"
            value={draft.password}
            onChange={(e) => setDraft({ ...draft, password: e.target.value })}
            style={field(170)}
          />
          <Select
            value={draft.role}
            onChange={(v) => setDraft({ ...draft, role: v })}
            options={ROLE_OPTS}
            minWidth={120}
          />
          <button className="btn" disabled={busy} onClick={add} style={primaryBtn(busy)}>
            Add user
          </button>
        </div>
      </div>

      {/* User table */}
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--muted)", fontSize: 11 }}>
              {["Email", "Name", "Role", "Status", "Last login", ""].map((h) => (
                <th key={h} style={{ padding: "10px 14px", fontWeight: 500, borderBottom: "1px solid var(--border)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users?.map((u) => (
              <tr key={u.id} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "10px 14px" }}>
                  <span className="mono">{u.email}</span>
                  {me?.user_id === u.id && (
                    <span className="chip" style={{ marginLeft: 8, fontSize: 10 }}>you</span>
                  )}
                </td>
                <td style={{ padding: "10px 14px", color: "var(--secondary)" }}>{u.name || "—"}</td>
                <td style={{ padding: "10px 14px" }}>
                  <div style={{ maxWidth: 120 }}>
                    <Select
                      value={u.role}
                      onChange={(v) => guarded(() => updateUser(u.id, { role: v }))}
                      options={ROLE_OPTS}
                      minWidth={110}
                    />
                  </div>
                </td>
                <td style={{ padding: "10px 14px" }}>
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => guarded(() => updateUser(u.id, { is_active: !u.is_active }))}
                    style={{ fontSize: 11, padding: "3px 9px" }}
                  >
                    <span style={{ color: u.is_active ? "var(--success)" : "var(--muted)" }}>
                      {u.is_active ? "● active" : "○ disabled"}
                    </span>
                  </button>
                </td>
                <td style={{ padding: "10px 14px", color: "var(--muted)", fontSize: 11.5 }}>
                  {u.last_login_at ? new Date(u.last_login_at + "Z").toLocaleString() : "never"}
                </td>
                <td style={{ padding: "10px 14px", textAlign: "right", whiteSpace: "nowrap" }}>
                  {pwFor === u.id ? (
                    <span style={{ display: "inline-flex", gap: 6 }}>
                      <input
                        type="password"
                        autoFocus
                        placeholder="new password"
                        value={pwDraft}
                        onChange={(e) => setPwDraft(e.target.value)}
                        style={field(140)}
                      />
                      <button className="btn" disabled={busy} onClick={() => resetPw(u.id)} style={{ fontSize: 11 }}>
                        save
                      </button>
                      <button className="btn" onClick={() => { setPwFor(null); setPwDraft(""); }} style={{ fontSize: 11 }}>
                        cancel
                      </button>
                    </span>
                  ) : (
                    <span style={{ display: "inline-flex", gap: 6 }}>
                      <button
                        className="btn"
                        onClick={() => { setPwFor(u.id); setPwDraft(""); setErr(null); }}
                        style={{ fontSize: 11 }}
                      >
                        reset password
                      </button>
                      <button
                        className="btn"
                        disabled={busy}
                        onClick={() => {
                          if (confirm(`Delete ${u.email}?`)) guarded(() => deleteUser(u.id));
                        }}
                        style={{ fontSize: 11, color: "var(--error)" }}
                      >
                        delete
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
            {users && users.length === 0 && (
              <tr>
                <td colSpan={6} style={{ padding: 20, textAlign: "center", color: "var(--muted)" }}>
                  No users yet. The first account you create becomes an admin.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 11, color: ROLE_COLOR.viewer, marginTop: 10 }}>
        The last active admin can't be deleted or demoted, to avoid a lock-out.
      </div>
    </div>
  );
}

function field(minWidth: number) {
  return {
    minWidth,
    boxSizing: "border-box" as const,
    background: "var(--bg-elev)",
    border: "1px solid var(--border-strong)",
    borderRadius: "var(--r-md)",
    padding: "7px 11px",
    color: "var(--text)",
    fontSize: 12.5,
    outline: "none",
  };
}

function primaryBtn(busy: boolean) {
  return {
    background: "var(--accent-soft)",
    border: "1px solid var(--accent-line)",
    color: "var(--accent-bright)",
    fontWeight: 600,
    opacity: busy ? 0.55 : 1,
  };
}
