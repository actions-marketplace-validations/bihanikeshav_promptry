import { useEffect, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageHeader, Select } from "../components/ui";
import { TemplateEditor } from "../components/TemplateEditor";
import { getConfig, getSuiteCandidates, createSuite, getSuiteDefinition, getRecordedContext } from "../api/client";
import type {
  SuiteAssertionType,
  SuiteCandidate,
  SuiteCaseInput,
  CreateSuiteResponse,
} from "../api/types";

/* ---- assertion metadata (mirrors Playground's rule rows) ---- */
interface AssertTypeDef {
  v: SuiteAssertionType;
  label: string;
  hint: string;
  /** value optional — falls back to the case's expected response at save time. */
  usesExpected?: boolean;
}
const ASSERT_TYPES: AssertTypeDef[] = [
  { v: "contains", label: "Must contain", hint: "keyword1, keyword2" },
  { v: "not_contains", label: "Must NOT contain", hint: "banned, term" },
  { v: "regex", label: "Regex match", hint: "^\\{.*\\}$" },
  { v: "exact", label: "Exact match", hint: "exact text (blank = expected)", usesExpected: true },
  { v: "semantic", label: "Semantic similarity", hint: "reference (blank = expected)", usesExpected: true },
  { v: "grounded", label: "Grounded in context", hint: "(checks answer uses context)", usesExpected: true },
];

const MODELS_FALLBACK = ["gpt-4o", "gpt-4o-mini", "claude-haiku-4-5", "claude-sonnet-4-5", "llama-3.3-70b"];

interface AssertDraft {
  id: number;
  type: SuiteAssertionType;
  value: string;
}
interface CaseDraft {
  id: number;
  input: string;
  context: string;
  expected: string;
  asserts: AssertDraft[];
  source?: string;
}

let uid = 1;
const nextId = () => uid++;

const emptyCase = (): CaseDraft => ({
  id: nextId(),
  input: "",
  context: "",
  expected: "",
  asserts: [],
});

/* ---- reusable assertion editor ---- */
function AssertionRows({
  asserts,
  onChange,
}: {
  asserts: AssertDraft[];
  onChange: (next: AssertDraft[]) => void;
}) {
  const add = () => onChange([...asserts, { id: nextId(), type: "contains", value: "" }]);
  const patch = (id: number, p: Partial<AssertDraft>) =>
    onChange(asserts.map((a) => (a.id === id ? { ...a, ...p } : a)));
  const remove = (id: number) => onChange(asserts.filter((a) => a.id !== id));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {asserts.map((a) => {
        const def = ASSERT_TYPES.find((t) => t.v === a.type) || ASSERT_TYPES[0];
        return (
          <div
            key={a.id}
            style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8, alignItems: "center" }}
          >
            <Select
              value={a.type}
              onChange={(v) => patch(a.id, { type: v as SuiteAssertionType })}
              minWidth={180}
              options={ASSERT_TYPES.map((t) => ({ value: t.v, label: t.label }))}
            />
            <input
              className="inp"
              value={a.value}
              onChange={(e) => patch(a.id, { value: e.target.value })}
              placeholder={def.hint}
              style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
            <button
              className="btn btn-ghost"
              onClick={() => remove(a.id)}
              style={{ color: "var(--muted)", padding: "4px 8px" }}
              title="Remove assertion"
            >
              ×
            </button>
          </div>
        );
      })}
      <button
        className="btn"
        onClick={add}
        style={{ alignSelf: "flex-start", color: "var(--accent)", borderColor: "var(--accent-line)" }}
      >
        + Add assertion
      </button>
    </div>
  );
}

/* ---- candidate picker (golden / feedback tabs) ---- */
function CandidatePicker({
  source,
  onAdd,
}: {
  source: "golden" | "feedback";
  onAdd: (cases: CaseDraft[]) => void;
}) {
  const [name, setName] = useState("");
  const [minRating, setMinRating] = useState<number | "">(source === "feedback" ? 1 : "");
  const [candidates, setCandidates] = useState<SuiteCandidate[]>([]);
  const [captureNote, setCaptureNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());

  const load = () => {
    setLoading(true);
    setErr(null);
    setPicked(new Set());
    getSuiteCandidates({
      source,
      name: name || undefined,
      minRating: source === "feedback" && minRating !== "" ? Number(minRating) : undefined,
      limit: 50,
    })
      .then((r) => {
        setCandidates(r.candidates);
        setCaptureNote(r.capture_note);
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  // Load once when the tab first mounts.
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (i: number) => {
    const next = new Set(picked);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    setPicked(next);
  };

  const addPicked = () => {
    const cases: CaseDraft[] = [...picked].map((i) => {
      const c = candidates[i];
      return {
        id: nextId(),
        input: c.question ?? "",
        context: c.context ?? "",
        expected: c.response ?? "",
        asserts: [],
        source: c.source,
      };
    });
    if (cases.length) onAdd(cases);
    setPicked(new Set());
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--secondary)" }}>
          Prompt name (optional)
          <input
            className="inp"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="filter by prompt…"
            style={{ width: 220, fontFamily: "var(--font-mono)", fontSize: 12 }}
          />
        </label>
        {source === "feedback" && (
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--secondary)" }}>
            Min rating
            <input
              className="inp"
              type="number"
              min={0}
              max={5}
              value={minRating}
              onChange={(e) => setMinRating(e.target.value === "" ? "" : Number(e.target.value))}
              style={{ width: 90, fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
          </label>
        )}
        <button className="btn" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Search"}
        </button>
      </div>

      {captureNote && (
        <div
          style={{
            padding: "9px 12px",
            borderRadius: 6,
            background: "var(--warning-soft)",
            border: "1px solid var(--warning)22",
            color: "var(--warning)",
            fontSize: 12,
          }}
        >
          {captureNote}
        </div>
      )}

      {err && (
        <div style={{ fontSize: 12, color: "var(--error)", fontFamily: "var(--font-mono)" }}>{err}</div>
      )}

      {!loading && !err && candidates.length === 0 && (
        <div style={{ padding: 20, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
          No {source} candidates found.
        </div>
      )}

      {candidates.length > 0 && (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 420, overflowY: "auto" }}>
            {candidates.map((c, i) => {
              const on = picked.has(i);
              const missing = !c.context || !c.response;
              return (
                <button
                  key={i}
                  onClick={() => toggle(i)}
                  style={{
                    textAlign: "left",
                    display: "grid",
                    gridTemplateColumns: "auto 1fr",
                    gap: 10,
                    padding: "10px 12px",
                    borderRadius: 6,
                    border: "1px solid " + (on ? "var(--accent-line)" : "var(--border)"),
                    background: on ? "var(--accent-soft)" : "var(--bg-elev)",
                    cursor: "pointer",
                  }}
                >
                  <span
                    style={{
                      width: 16,
                      height: 16,
                      borderRadius: 4,
                      border: "1px solid " + (on ? "var(--accent)" : "var(--border-strong)"),
                      background: on ? "var(--accent)" : "transparent",
                      color: "var(--bg)",
                      fontSize: 11,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      marginTop: 2,
                    }}
                  >
                    {on ? "✓" : ""}
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, color: "var(--text)", marginBottom: 3 }}>
                      {c.question || <span style={{ color: "var(--muted)" }}>(no question text)</span>}
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
                      {c.prompt_name && <span>{c.prompt_name}</span>}
                      <span>src: {c.source}</span>
                      {missing && <span style={{ color: "var(--warning)" }}>missing {[!c.context && "context", !c.response && "response"].filter(Boolean).join(" + ")}</span>}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
          <button
            className="btn btn-primary"
            onClick={addPicked}
            disabled={picked.size === 0}
            style={{ alignSelf: "flex-start" }}
          >
            Add {picked.size || ""} selected to suite
          </button>
        </>
      )}
    </div>
  );
}

/* ---- main page ---- */
export default function SuiteCreate() {
  const [tab, setTab] = useState<"manual" | "golden" | "feedback">("manual");
  const [name, setName] = useState("");
  const [model, setModel] = useState("gpt-4o-mini");
  const [prompt, setPrompt] = useState("");
  const [models, setModels] = useState<string[]>(MODELS_FALLBACK);
  const [suiteAsserts, setSuiteAsserts] = useState<AssertDraft[]>([]);
  const [cases, setCases] = useState<CaseDraft[]>([emptyCase()]);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<CreateSuiteResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [params] = useSearchParams();
  const editName = params.get("edit");
  const [editing, setEditing] = useState(false);
  const [contextSource, setContextSource] = useState("");

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        if (cfg.models?.length) {
          const ids = cfg.models.map((m) => m.id);
          setModels(ids);
          setModel((cur) => (ids.includes(cur) ? cur : ids[0]));
        }
      })
      .catch(() => {});
  }, []);

  // Edit mode: load an existing YAML suite's definition into the form.
  useEffect(() => {
    if (!editName) return;
    getSuiteDefinition(editName)
      .then((r) => {
        if (!r.editable || !r.definition) {
          setError(
            `"${editName}" is defined in ${r.source === "python" ? "Python (evals.py)" : "an unknown source"} and can't be edited here. Suites created in the dashboard live in evals.yaml.`,
          );
          return;
        }
        const d = r.definition;
        setEditing(true);
        setName(d.name);
        if (d.model) setModel(d.model);
        if (d.prompt) setPrompt(d.prompt);
        setContextSource(d.name);
        setCases(
          (d.cases || []).map((c) => ({
            id: nextId(),
            input: c.input || "",
            context: c.context || "",
            expected: "",
            asserts: (c.expect || []).map((e) => ({
              id: nextId(),
              type: e.type,
              value: typeof e.value === "string" ? e.value : JSON.stringify(e.value ?? ""),
            })),
          })),
        );
      })
      .catch((e) => setError(String(e)));
  }, [editName]);

  const pullContext = async (caseId: number) => {
    const src = contextSource.trim();
    if (!src) return;
    try {
      const r = await getRecordedContext(src);
      if (r.found && r.context) patchCase(caseId, { context: r.context });
      else setError(`No recorded context found for "${src}". Your app must call track_context("${src}", …).`);
    } catch (e) {
      setError(String(e));
    }
  };

  const addCase = () => setCases((cs) => [...cs, emptyCase()]);
  const patchCase = (id: number, p: Partial<CaseDraft>) =>
    setCases((cs) => cs.map((c) => (c.id === id ? { ...c, ...p } : c)));
  const removeCase = (id: number) => setCases((cs) => cs.filter((c) => c.id !== id));
  const addCandidates = (draft: CaseDraft[]) => {
    setCases((cs) => {
      // Drop a leading blank starter case when importing candidates.
      const base = cs.length === 1 && !cs[0].input && !cs[0].context && !cs[0].expected && cs[0].asserts.length === 0 ? [] : cs;
      return [...base, ...draft];
    });
    setTab("manual");
  };

  /** Build the POST payload. Suite-level assertions apply to every case; an
   *  expected-response text with no matching assertion becomes a semantic one so
   *  it is never silently dropped (the contract body has no `expected` field). */
  const buildCases = (): SuiteCaseInput[] =>
    cases
      .filter((c) => c.input.trim())
      .map((c) => {
        const resolve = (a: AssertDraft) => {
          const def = ASSERT_TYPES.find((t) => t.v === a.type);
          const value = a.value.trim() || (def?.usesExpected ? c.expected.trim() : "");
          return { type: a.type, value };
        };
        const expect = [...c.asserts, ...suiteAsserts].map(resolve);
        const hasRef = expect.some((e) => e.type === "semantic" || e.type === "exact" || e.type === "grounded");
        if (c.expected.trim() && !hasRef) {
          expect.push({ type: "semantic", value: c.expected.trim() });
        }
        const out: SuiteCaseInput = { input: c.input.trim(), expect };
        if (c.context.trim()) out.context = c.context.trim();
        return out;
      });

  const validCaseCount = cases.filter((c) => c.input.trim()).length;
  const canSave = name.trim() && prompt.trim() && validCaseCount > 0 && !saving;

  const save = async () => {
    setSaving(true);
    setError(null);
    setResult(null);
    try {
      const res = await createSuite({
        name: name.trim(),
        model,
        prompt,
        cases: buildCases(),
        overwrite: editing,
      });
      if (res.ok === false) {
        setError(res.error || "Suite creation failed.");
      } else {
        setResult(res);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow={editing ? "~/promptry · edit suite" : "~/promptry · new suite"}
        title={editing ? `Edit suite · ${name}` : "Create eval suite"}
        description="Assemble RAG test cases — question, retrieved context, expected response — from scratch or from real golden examples and feedback, then attach assertions and save."
        tags={[`${validCaseCount} case${validCaseCount === 1 ? "" : "s"}`]}
        actions={
          <button className="btn btn-primary" onClick={save} disabled={!canSave}>
            {saving ? "Saving…" : "Save suite"}
          </button>
        }
      />

      {/* success / error banners */}
      {result && (
        <div
          className="card enter"
          style={{ padding: 14, marginBottom: 14, borderColor: "var(--success)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
        >
          <span style={{ color: "var(--success)", fontSize: 13 }}>
            Suite <strong className="mono">{result.name || name}</strong> saved
            {result.cases != null ? ` · ${result.cases} cases` : ""}.
          </span>
          <Link className="btn" to="/evals">
            View in Evals →
          </Link>
        </div>
      )}
      {error && (
        <div
          className="card"
          style={{ padding: 14, marginBottom: 14, borderColor: "var(--error)", color: "var(--error)", fontSize: 12.5, fontFamily: "var(--font-mono)" }}
        >
          {error}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.4fr) minmax(300px, 0.9fr)", gap: 14 }}>
        {/* LEFT: sources + cases */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          {/* source tabs */}
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            <div style={{ display: "flex", borderBottom: "1px solid var(--border)", background: "var(--bg-elev)" }}>
              {(
                [
                  { v: "manual" as const, l: "Manual" },
                  { v: "golden" as const, l: "Golden examples" },
                  { v: "feedback" as const, l: "From feedback" },
                ]
              ).map((t) => {
                const active = tab === t.v;
                return (
                  <button
                    key={t.v}
                    onClick={() => setTab(t.v)}
                    className="btn btn-ghost"
                    style={{
                      borderRadius: 0,
                      borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
                      color: active ? "var(--text)" : "var(--secondary)",
                      padding: "10px 16px",
                      fontWeight: active ? 600 : 500,
                      fontSize: 12.5,
                    }}
                  >
                    {t.l}
                  </button>
                );
              })}
            </div>
            <div style={{ padding: 14 }}>
              {tab === "manual" && (
                <div style={{ fontSize: 12.5, color: "var(--secondary)", lineHeight: 1.6 }}>
                  Build cases by hand below, or pull real examples from the{" "}
                  <button className="btn btn-ghost" style={{ padding: 0, color: "var(--accent)" }} onClick={() => setTab("golden")}>
                    Golden
                  </button>{" "}
                  and{" "}
                  <button className="btn btn-ghost" style={{ padding: 0, color: "var(--accent)" }} onClick={() => setTab("feedback")}>
                    Feedback
                  </button>{" "}
                  tabs.
                </div>
              )}
              {tab === "golden" && <CandidatePicker source="golden" onAdd={addCandidates} />}
              {tab === "feedback" && <CandidatePicker source="feedback" onAdd={addCandidates} />}
            </div>
          </div>

          {/* cases */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)" }}>
              Cases
            </div>
            <button className="btn" onClick={addCase} style={{ color: "var(--accent)", borderColor: "var(--accent-line)" }}>
              + Add case
            </button>
          </div>

          {cases.map((c, idx) => (
            <div key={c.id} className="card" style={{ padding: 14 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                <span className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                  case {idx + 1}
                  {c.source && <span style={{ marginLeft: 8, color: "var(--accent)" }}>· {c.source}</span>}
                </span>
                {cases.length > 1 && (
                  <button className="btn btn-ghost" onClick={() => removeCase(c.id)} style={{ color: "var(--muted)", padding: "2px 8px" }}>
                    remove
                  </button>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <Field label="Question (input)">
                  <textarea
                    className="inp"
                    value={c.input}
                    onChange={(e) => patchCase(c.id, { input: e.target.value })}
                    rows={2}
                    style={{ width: "100%" }}
                    placeholder="What the user asks…"
                  />
                </Field>
                <Field label="Retrieved context">
                  <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}>
                    <button
                      className="btn btn-ghost"
                      onClick={() => pullContext(c.id)}
                      disabled={!contextSource.trim()}
                      title={contextSource.trim() ? `Fill from the latest track_context("${contextSource.trim()}", …)` : "Set a context-source prompt in suite settings first"}
                      style={{ fontSize: 10.5, padding: "2px 8px", color: "var(--accent)" }}
                    >
                      ⭳ from logs
                    </button>
                  </div>
                  <textarea
                    className="inp"
                    value={c.context}
                    onChange={(e) => patchCase(c.id, { context: e.target.value })}
                    rows={3}
                    style={{ width: "100%" }}
                    placeholder="RAG documents / retrieved passages…"
                  />
                </Field>
                <Field label="Expected response">
                  <textarea
                    className="inp"
                    value={c.expected}
                    onChange={(e) => patchCase(c.id, { expected: e.target.value })}
                    rows={2}
                    style={{ width: "100%" }}
                    placeholder="Reference answer — used by semantic / exact / grounded assertions."
                  />
                </Field>
                <Field label="Assertions (this case)">
                  <AssertionRows asserts={c.asserts} onChange={(next) => patchCase(c.id, { asserts: next })} />
                </Field>
              </div>
            </div>
          ))}
        </div>

        {/* RIGHT: suite-level settings */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14, alignSelf: "start" }}>
          <div className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)" }}>
              Suite
            </div>
            <Field label="Name">
              <input
                className="inp"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="support.rag.smoke"
                style={{ width: "100%", fontFamily: "var(--font-mono)", fontSize: 12.5 }}
              />
            </Field>
            <Field label="Model">
              <Select
                value={model}
                onChange={setModel}
                minWidth={220}
                searchable
                options={models.map((m) => ({ value: m, label: m }))}
              />
            </Field>
            <Field label="Prompt template">
              <TemplateEditor value={prompt} onChange={setPrompt} minHeight={140} placeholder="You are a support assistant. Context:\n{{context}}\n\nQ: {{question}}" />
            </Field>
            <Field label="Context source (for ⭳ from logs)">
              <input
                className="inp"
                value={contextSource}
                onChange={(e) => setContextSource(e.target.value)}
                placeholder="prompt name, e.g. rag.answer"
                style={{ width: "100%", fontFamily: "var(--font-mono)", fontSize: 12 }}
              />
              <div style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 4 }}>
                Pulls the latest context your app recorded via track_context for this prompt.
              </div>
            </Field>
          </div>

          <div className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)" }}>
              Suite-level assertions
            </div>
            <div style={{ fontSize: 11.5, color: "var(--secondary)" }}>
              Applied to every case, on top of each case's own assertions.
            </div>
            <AssertionRows asserts={suiteAsserts} onChange={setSuiteAsserts} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div
        style={{
          fontSize: 10,
          color: "var(--muted)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          marginBottom: 6,
          fontFamily: "var(--font-mono)",
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}
