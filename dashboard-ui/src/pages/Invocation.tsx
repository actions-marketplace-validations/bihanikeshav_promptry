import { useParams, Link, useNavigate, useLocation } from "react-router-dom";
import { Breadcrumbs, PageHeader } from "../components/ui";
import { relTime } from "../utils";
import { getInvocation, getInvocationScan } from "../api/client";
import { useCached } from "../lib/cache";
import type { InvocationDetail, PiiScan, PiiFinding } from "../api/types";

const usd = (n: number | null | undefined) => (n == null ? "—" : "$" + n.toFixed(n < 0.01 ? 6 : 4));

export default function Invocation() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const location = useLocation();
  // The path we drilled down from (Cost or Prompts), passed via nav state.
  const fromCrumbs = (location.state as { from?: { label: string; to?: string }[] } | null)?.from;
  const { data: d } = useCached<InvocationDetail>(`invocation:${id}`, () => getInvocation(Number(id)));
  const { data: scan } = useCached<PiiScan>(`invocation-scan:${id}`, () => getInvocationScan(Number(id)));

  if (!d) return <div><PageHeader eyebrow="~/promptry · invocation" title={`#${id}`} description="Loading…" /></div>;

  const m = d.metadata as Record<string, number | string>;
  const b = d.breakdown;
  const meta = [
    ["model", m.model ?? "—"],
    ["tokens in", m.tokens_in ?? m.prompt_tokens ?? "—"],
    ["tokens out", m.tokens_out ?? m.completion_tokens ?? "—"],
    ["cost", m.cost != null ? usd(Number(m.cost)) : "—"],
    ["latency", m.latency_ms != null ? Math.round(Number(m.latency_ms)) + "ms" : "—"],
  ];

  return (
    <div>
      <Breadcrumbs items={[
        ...(fromCrumbs && fromCrumbs.length
          ? fromCrumbs
          : [{ label: "prompts", to: "/prompts" }, { label: d.prompt_name, to: `/prompts/${encodeURIComponent(d.prompt_name)}` }]),
        { label: `invocation #${d.id}` },
      ]} />
      <PageHeader
        eyebrow="~/promptry · invocation"
        title={`#${d.id}`}
        description={`${d.prompt_name}${d.prompt_version != null ? ` · v${d.prompt_version}` : ""} · ${relTime(d.created_at)}`}
        actions={<button className="btn" onClick={() => nav(-1)}>← Back</button>}
      />

      {/* meta strip */}
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginBottom: 18 }}>
        {meta.map(([k, v]) => (
          <div key={k}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em", fontFamily: "var(--font-mono)" }}>{k}</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text)", marginTop: 2 }}>{String(v)}</div>
          </div>
        ))}
      </div>

      {/* PII / secret scan of captured text */}
      {scan && scan.total > 0 && <PiiPanel scan={scan} />}

      {/* template vs data cost breakdown */}
      {b && b.available === false && (
        <div className="card" style={{ padding: "12px 16px", marginBottom: 18, fontSize: 12, color: "var(--muted)" }}>
          {b.reason}
        </div>
      )}
      {b && b.available && ((b.template_tokens || 0) > 0 || (b.data_tokens || 0) > 0) && (
        <div className="card" style={{ padding: 16, marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Where the input cost went <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>· estimated (~4 chars/token)</span></div>
          <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 3, marginBottom: 12 }}>
            How much of this call is fixed template overhead vs the variable payload you fed in.
          </div>
          {(() => {
            const total = (b.template_tokens || 0) + (b.data_tokens || 0) || 1;
            const tPct = ((b.template_tokens || 0) / total) * 100;
            return (
              <>
                <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", border: "1px solid var(--border)" }}>
                  <div style={{ width: tPct + "%", background: "var(--accent)" }} title="template" />
                  <div style={{ width: 100 - tPct + "%", background: "var(--text-dim)", opacity: 0.4 }} title="payload" />
                </div>
                <div style={{ display: "flex", gap: 24, marginTop: 12 }}>
                  <Split color="var(--accent)" label="Template (fixed)" tokens={b.template_tokens ?? 0} cost={b.template_cost ?? null} />
                  <Split color="var(--text-dim)" label="Payload (variable)" tokens={b.data_tokens ?? 0} cost={b.data_cost ?? null} />
                  <Split color="var(--success)" label="Response" tokens={b.tokens_out ?? 0} cost={b.output_cost ?? null} />
                </div>
              </>
            );
          })()}
        </div>
      )}

      {/* feedback */}
      {d.feedback.length > 0 && (
        <div className="card" style={{ padding: 16, marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Feedback</div>
          {d.feedback.map((f, i) => (
            <div key={i} style={{ fontSize: 12.5, color: "var(--text-dim)", marginBottom: 6, padding: "8px 11px", background: "var(--bg-elev)", borderRadius: 6 }}>
              {f.rating != null && <span style={{ color: "var(--accent)", fontWeight: 600, marginRight: 8 }}>★ {f.rating}</span>}
              {f.comment || <span style={{ color: "var(--muted)" }}>(no comment)</span>}
              {f.source && <span style={{ color: "var(--muted)", marginLeft: 8, fontSize: 10.5 }}>via {f.source}</span>}
            </div>
          ))}
        </div>
      )}

      {/* request / response */}
      <Block label="Request" text={d.input_text} color="var(--text-dim)" />
      <div style={{ height: 14 }} />
      <Block label="Response" text={d.output_text} color="var(--text)" />

      <div style={{ marginTop: 14, fontSize: 12 }}>
        <Link to={`/prompts/${encodeURIComponent(d.prompt_name)}`} style={{ color: "var(--accent)", textDecoration: "none" }}>
          View prompt · {d.prompt_name} →
        </Link>
      </div>
    </div>
  );
}

function PiiPanel({ scan }: { scan: PiiScan }) {
  const sevColor = scan.worst_severity === "high" ? "var(--error)" : "var(--warning)";
  const label = (t: string) => t.replace(/_/g, " ");
  const Side = ({ title, findings }: { title: string; findings: PiiFinding[] }) =>
    findings.length === 0 ? null : (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <span style={{ fontSize: 10.5, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em", fontFamily: "var(--font-mono)", minWidth: 56 }}>{title}</span>
        {findings.map((f) => (
          <span key={f.type} className="chip mono" style={{ fontSize: 10.5, color: f.category === "secret" ? "var(--error)" : "var(--warning)", display: "inline-flex", gap: 6, alignItems: "center" }}>
            {label(f.type)}{f.count > 1 ? ` ×${f.count}` : ""}
            <span style={{ color: "var(--muted)" }}>{f.sample}</span>
          </span>
        ))}
      </div>
    );
  return (
    <div className="card" style={{ padding: 16, marginBottom: 18, borderColor: sevColor }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 9, height: 9, borderRadius: 999, background: sevColor }} />
        <div style={{ fontSize: 13, fontWeight: 600 }}>
          {scan.has_secret ? "Secrets detected in captured text" : "PII detected in captured text"}
        </div>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 3, marginBottom: 12 }}>
        Regex tripwire over the request/response. Matches are masked — fix at the source or disable capture for this prompt.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <Side title="request" findings={scan.input} />
        <Side title="response" findings={scan.output} />
      </div>
    </div>
  );
}

function Split({ color, label, tokens, cost }: { color: string; label: string; tokens: number; cost: number | null }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--muted)" }}>
        <span style={{ width: 9, height: 9, borderRadius: 2, background: color }} /> {label}
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)", marginTop: 3 }}>
        {tokens.toLocaleString()} tok <span style={{ color: "var(--accent)", fontWeight: 500, fontSize: 12.5 }}>{usd(cost)}</span>
      </div>
    </div>
  );
}

function Block({ label, text, color }: { label: string; text: string | null; color: string }) {
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)" }}>{label}</div>
      <pre style={{ margin: 0, fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word", color, padding: 14, maxHeight: 460, overflow: "auto" }}>
        {text || "(not captured)"}
      </pre>
    </div>
  );
}
