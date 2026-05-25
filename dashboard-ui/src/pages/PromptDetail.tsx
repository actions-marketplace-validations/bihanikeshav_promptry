import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Breadcrumbs, PageHeader } from "../components/ui";
import { cls, relTime } from "../utils";
import {
  getPromptVersions,
  getPromptDiff,
  getPromptContent,
  savePromptContent,
} from "../api/client";
import type { PromptVersion, DiffResponse } from "../api/types";

type View = "current" | "diff" | "edit";

export default function PromptDetail() {
  const { name = "" } = useParams();
  const promptName = decodeURIComponent(name);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [content, setContent] = useState<string>("");
  const [view, setView] = useState<View>("current");
  const [draft, setDraft] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  function reloadVersions(selectLatest = false) {
    return getPromptVersions(promptName)
      .then((r) => {
        setVersions(r.versions);
        if ((selectLatest || selected == null) && r.versions.length > 0) {
          setSelected(r.versions[0].version);
        }
      })
      .catch(() => setVersions([]));
  }

  useEffect(() => {
    reloadVersions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptName]);

  // Full content of the selected version — the actual prompt text.
  useEffect(() => {
    if (selected == null) return;
    getPromptContent(promptName, selected)
      .then((r) => {
        setContent(r.content);
        setDraft(r.content);
      })
      .catch(() => setContent(""));
  }, [promptName, selected]);

  // Diff against the previous version (if any).
  useEffect(() => {
    if (selected == null) return;
    const idx = versions.findIndex((v) => v.version === selected);
    const prev = versions[idx + 1];
    if (!prev) {
      setDiff(null);
      return;
    }
    getPromptDiff(promptName, prev.version, selected).then(setDiff).catch(() => setDiff(null));
  }, [promptName, selected, versions]);

  const isLatest = versions.length > 0 && selected === versions[0].version;

  async function handleSave() {
    setSaving(true);
    setSaveErr(null);
    try {
      const res = await savePromptContent(promptName, draft);
      await reloadVersions(true);
      setSelected(res.version);
      setView("current");
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const tab = (id: View, label: string, disabled = false) => (
    <button
      onClick={() => !disabled && setView(id)}
      disabled={disabled}
      style={{
        padding: "5px 12px",
        borderRadius: 6,
        fontSize: 12,
        fontWeight: view === id ? 600 : 500,
        cursor: disabled ? "not-allowed" : "pointer",
        color: disabled ? "var(--muted)" : view === id ? "var(--text)" : "var(--text-dim)",
        background: view === id ? "var(--bg-elev)" : "transparent",
        border: view === id ? "1px solid var(--border)" : "1px solid transparent",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {label}
    </button>
  );

  return (
    <div>
      <Breadcrumbs items={[{ label: "prompts", to: "/prompts" }, { label: promptName }]} />
      <PageHeader eyebrow="Prompt" title={promptName} description={`${versions.length} versions tracked.`} />

      <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", gap: 12 }}>
        {/* Versions rail */}
        <div className="card" style={{ padding: 8, height: "fit-content" }}>
          <div
            style={{
              padding: "6px 8px",
              fontSize: 11,
              color: "var(--muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              fontFamily: "var(--font-mono)",
            }}
          >
            Versions
          </div>
          {versions.map((v, i) => (
            <div
              key={v.version}
              onClick={() => setSelected(v.version)}
              style={{
                padding: "9px 10px",
                borderRadius: 6,
                cursor: "pointer",
                marginBottom: 2,
                background: selected === v.version ? "var(--bg-elev)" : "transparent",
                border: selected === v.version ? "1px solid var(--border)" : "1px solid transparent",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="mono" style={{ fontSize: 13, color: "var(--text)", fontWeight: 600 }}>
                  v{v.version}
                  {i === 0 && (
                    <span style={{ fontSize: 9, color: "var(--success)", marginLeft: 6, letterSpacing: "0.04em" }}>
                      LATEST
                    </span>
                  )}
                </span>
                <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>
                  {v.hash.slice(0, 7)}
                </span>
              </div>
              <div style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 3, fontFamily: "var(--font-mono)" }}>
                {relTime(v.created_at)}
              </div>
            </div>
          ))}
          {versions.length === 0 && (
            <div style={{ padding: 12, color: "var(--muted)", fontSize: 12 }}>Loading…</div>
          )}
        </div>

        {/* Content panel */}
        <div className="card" style={{ padding: 0 }}>
          <div
            style={{
              padding: "12px 16px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
            }}
          >
            <div style={{ display: "flex", gap: 4 }}>
              {tab("current", selected != null ? `Current · v${selected}` : "Current")}
              {tab("diff", "Diff", !diff)}
              {tab("edit", "Edit")}
            </div>
            {view === "diff" && diff && (
              <div style={{ display: "flex", gap: 6 }}>
                <span className="chip mono" style={{ fontSize: 10, color: "var(--error)" }}>
                  − {diff.deletions}
                </span>
                <span className="chip mono" style={{ fontSize: 10, color: "var(--success)" }}>
                  + {diff.additions}
                </span>
              </div>
            )}
          </div>

          {/* CURRENT — the actual prompt text */}
          {view === "current" && (
            <pre
              style={{
                margin: 0,
                padding: "16px 18px",
                fontSize: 12.5,
                lineHeight: 1.6,
                color: "var(--text)",
                fontFamily: "var(--font-mono)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {content || "Loading…"}
            </pre>
          )}

          {/* DIFF */}
          {view === "diff" && (
            <div style={{ padding: "8px 0" }}>
              {!diff ? (
                <div style={{ padding: 24, color: "var(--muted)", fontSize: 12, textAlign: "center" }}>
                  Earliest version — no diff available.
                </div>
              ) : (
                diff.lines.map((l, i) => (
                  <div key={i} className={cls("diff-line", l.type)}>
                    <span className="ln">{l.old_num ?? ""}</span>
                    <span className="ln">{l.new_num ?? ""}</span>
                    <pre
                      style={{
                        color:
                          l.type === "added"
                            ? "var(--success)"
                            : l.type === "deleted"
                              ? "var(--error)"
                              : "var(--text-dim)",
                      }}
                    >
                      {l.type === "added" ? "+ " : l.type === "deleted" ? "- " : "  "}
                      {l.content || " "}
                    </pre>
                  </div>
                ))
              )}
            </div>
          )}

          {/* EDIT — save creates a new version the app picks up live */}
          {view === "edit" && (
            <div style={{ padding: 16 }}>
              {!isLatest && (
                <div
                  style={{
                    fontSize: 11.5,
                    color: "var(--muted)",
                    marginBottom: 10,
                    padding: "8px 10px",
                    borderRadius: 6,
                    background: "var(--bg-elev)",
                  }}
                >
                  Editing from v{selected}. Saving appends a new latest version (it won't overwrite history).
                </div>
              )}
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                style={{
                  width: "100%",
                  minHeight: 360,
                  resize: "vertical",
                  padding: "12px 14px",
                  fontSize: 12.5,
                  lineHeight: 1.6,
                  fontFamily: "var(--font-mono)",
                  color: "var(--text)",
                  background: "var(--bg)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  outline: "none",
                }}
              />
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
                <button
                  onClick={handleSave}
                  disabled={saving || !draft.trim() || draft === content}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 7,
                    fontSize: 12.5,
                    fontWeight: 600,
                    cursor: saving || draft === content ? "not-allowed" : "pointer",
                    color: "var(--bg)",
                    background: draft === content || !draft.trim() ? "var(--muted)" : "var(--accent)",
                    border: "none",
                    opacity: saving ? 0.7 : 1,
                  }}
                >
                  {saving ? "Saving…" : "Save as new version"}
                </button>
                {draft !== content && (
                  <button
                    onClick={() => setDraft(content)}
                    style={{
                      padding: "8px 14px",
                      borderRadius: 7,
                      fontSize: 12.5,
                      cursor: "pointer",
                      color: "var(--text-dim)",
                      background: "transparent",
                      border: "1px solid var(--border)",
                    }}
                  >
                    Reset
                  </button>
                )}
                {saveErr && <span style={{ fontSize: 11.5, color: "var(--error)" }}>{saveErr}</span>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
