import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { scoreColor } from "../utils";

/* ---------------- StatusPill ---------------- */
export type StatusKind =
  | "pass"
  | "fail"
  | "stable"
  | "drifting"
  | "regression"
  | "improved"
  | "unchanged"
  | "regressed";

const STATUS_MAP: Record<StatusKind, { color: string; bg: string; label: string }> = {
  pass: { color: "var(--success)", bg: "var(--success-soft)", label: "PASS" },
  fail: { color: "var(--error)", bg: "var(--error-soft)", label: "FAIL" },
  stable: { color: "var(--success)", bg: "var(--success-soft)", label: "stable" },
  drifting: { color: "var(--warning)", bg: "var(--warning-soft)", label: "drifting" },
  regression: { color: "var(--error)", bg: "var(--error-soft)", label: "REGRESSION" },
  improved: { color: "var(--success)", bg: "var(--success-soft)", label: "improved" },
  unchanged: { color: "var(--secondary)", bg: "rgba(255,255,255,0.04)", label: "unchanged" },
  regressed: { color: "var(--error)", bg: "var(--error-soft)", label: "regressed" },
};

export function StatusPill({ status }: { status: StatusKind }) {
  const s = STATUS_MAP[status] || STATUS_MAP.unchanged;
  return (
    <span
      className="pill pill-dot"
      style={{ color: s.color, background: s.bg, border: `1px solid ${s.color}22` }}
    >
      {s.label}
    </span>
  );
}

/* ---------------- Sparkline ---------------- */
export function Sparkline({
  scores,
  width = 92,
  height = 24,
  showArea = true,
}: {
  scores: number[] | null | undefined;
  width?: number;
  height?: number;
  showArea?: boolean;
}) {
  if (!scores || scores.length < 2) {
    return (
      <svg width={width} height={height}>
        <text x={width / 2} y={height / 2 + 3} textAnchor="middle" fill="var(--muted)" fontSize="10">
          —
        </text>
      </svg>
    );
  }
  const pad = 2;
  const iw = width - pad * 2;
  const ih = height - pad * 2;
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const range = max - min || 1;
  const pts = scores.map((s, i) => {
    const x = pad + (i / (scores.length - 1)) * iw;
    const y = pad + (1 - (s - min) / range) * ih;
    return [x, y] as const;
  });
  const poly = pts.map((p) => p.join(",")).join(" ");
  const area = `${pad},${pad + ih} ${poly} ${pad + iw},${pad + ih}`;
  const last = scores[scores.length - 1];
  const c = scoreColor(last);
  const gid = "sg-" + Math.round(last * 1000) + "-" + Math.round(Math.random() * 10000);
  const lastPt = pts[pts.length - 1];
  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <defs>
        <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={c} stopOpacity="0.25" />
          <stop offset="100%" stopColor={c} stopOpacity="0" />
        </linearGradient>
      </defs>
      {showArea && <polygon points={area} fill={`url(#${gid})`} />}
      <polyline
        points={poly}
        fill="none"
        stroke={c}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={lastPt[0]} cy={lastPt[1]} r="2" fill={c} stroke="var(--bg)" strokeWidth="1" />
    </svg>
  );
}

/* ---------------- ScoreChart (history with release markers) ---------------- */
export interface ScorePoint {
  label: string;
  score: number;
  runId?: number;
  promptVersion?: number | null;
  modelVersion?: string | null;
}
export interface Marker {
  index: number;
  label: string;
}

export function ScoreChart({
  data,
  height = 180,
  markers = [],
}: {
  data: ScorePoint[];
  height?: number;
  markers?: Marker[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const [hoverI, setHoverI] = useState<number | null>(null);

  if (!data || data.length === 0) return <div ref={ref} style={{ height }} />;
  const pad = { l: 38, r: 14, t: 14, b: 26 };
  const iw = Math.max(40, w - pad.l - pad.r);
  const ih = height - pad.t - pad.b;
  const min = 0.3;
  const max = 1.0;
  const xs = (i: number) => pad.l + (i / (data.length - 1 || 1)) * iw;
  const ys = (s: number) => pad.t + (1 - (s - min) / (max - min)) * ih;
  const poly = data.map((d, i) => `${xs(i)},${ys(d.score)}`).join(" ");
  const area = `${pad.l},${pad.t + ih} ${poly} ${pad.l + iw},${pad.t + ih}`;
  const ticks = [0.4, 0.6, 0.8, 1.0];

  return (
    <div ref={ref} style={{ position: "relative", width: "100%", height }}>
      <svg
        width={w}
        height={height}
        style={{ display: "block" }}
        onMouseLeave={() => setHoverI(null)}
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const x = e.clientX - rect.left;
          const frac = Math.max(0, Math.min(1, (x - pad.l) / iw));
          setHoverI(Math.round(frac * (data.length - 1)));
        }}
      >
        <defs>
          <linearGradient id="areaGrad" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.24" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={pad.l + iw} y1={ys(t)} y2={ys(t)} stroke="var(--border)" strokeDasharray="2 3" />
            <text x={pad.l - 8} y={ys(t) + 3} textAnchor="end" fill="var(--muted)" fontSize="10" fontFamily="var(--font-mono)">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        <line x1={pad.l} x2={pad.l + iw} y1={ys(0.8)} y2={ys(0.8)} stroke="var(--success)" strokeOpacity="0.25" />
        <text x={pad.l + iw - 2} y={ys(0.8) - 4} textAnchor="end" fontSize="9" fill="var(--success)" opacity="0.7" fontFamily="var(--font-mono)">
          target 80%
        </text>
        {markers.map((m, i) => {
          const idx = Math.max(0, Math.min(data.length - 1, m.index));
          const x = xs(idx);
          return (
            <g key={i}>
              <line x1={x} x2={x} y1={pad.t} y2={pad.t + ih} stroke="var(--accent)" strokeOpacity="0.3" strokeDasharray="3 3" />
              <rect x={x - 2} y={pad.t - 2} width="4" height="4" fill="var(--accent)" />
              <text x={x + 4} y={pad.t + 8} fontSize="9.5" fontFamily="var(--font-mono)" fill="var(--accent)">
                {m.label}
              </text>
            </g>
          );
        })}
        <polygon points={area} fill="url(#areaGrad)" />
        <polyline points={poly} fill="none" stroke="var(--accent)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
        {data.map((d, i) => (
          <circle
            key={i}
            cx={xs(i)}
            cy={ys(d.score)}
            r={hoverI === i ? 3.5 : 1.8}
            fill={hoverI === i ? "var(--accent)" : "var(--bg)"}
            stroke="var(--accent)"
            strokeWidth="1.25"
          />
        ))}
        {hoverI != null && (
          <line x1={xs(hoverI)} x2={xs(hoverI)} y1={pad.t} y2={pad.t + ih} stroke="var(--text-dim)" strokeOpacity="0.35" />
        )}
      </svg>
      {hoverI != null && (
        <div
          style={{
            position: "absolute",
            left: Math.min(w - 170, xs(hoverI) + 10),
            top: Math.max(4, ys(data[hoverI].score) - 48),
            background: "var(--bg-elev)",
            border: "1px solid var(--border-strong)",
            borderRadius: 6,
            padding: "6px 8px",
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            pointerEvents: "none",
            minWidth: 150,
            zIndex: 2,
          }}
        >
          <div style={{ color: "var(--secondary)", marginBottom: 2 }}>{data[hoverI].label}</div>
          <div style={{ color: "var(--text)", fontWeight: 600, fontSize: 13 }}>
            {(data[hoverI].score * 100).toFixed(1)}%
          </div>
          {data[hoverI].promptVersion != null && (
            <div style={{ color: "var(--muted)" }}>
              prompt v{data[hoverI].promptVersion} · {data[hoverI].modelVersion}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------------- BarChart ---------------- */
export function BarChart<T extends Record<string, any>>({
  data,
  height = 200,
  valueKey,
  secondaryKey,
  labelKey = "date",
  format = (v: number) => "$" + v.toFixed(2),
}: {
  data: T[];
  height?: number;
  valueKey: keyof T;
  secondaryKey?: keyof T;
  labelKey?: keyof T;
  format?: (v: number) => string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const [hoverI, setHoverI] = useState<number | null>(null);

  if (!data || !data.length) return <div ref={ref} style={{ height }} />;
  const pad = { l: 46, r: 14, t: 14, b: 28 };
  const iw = Math.max(40, w - pad.l - pad.r);
  const ih = height - pad.t - pad.b;
  const max = Math.max(...data.map((d) => Number(d[valueKey]) || 0)) * 1.15 || 1;
  const bw = iw / data.length;
  const ticks = [0, max / 2, max];

  return (
    <div ref={ref} style={{ position: "relative", width: "100%", height }}>
      <svg width={w} height={height} style={{ display: "block" }} onMouseLeave={() => setHoverI(null)}>
        {ticks.map((t, i) => {
          const y = pad.t + (1 - t / max) * ih;
          return (
            <g key={i}>
              <line x1={pad.l} x2={pad.l + iw} y1={y} y2={y} stroke="var(--border)" strokeDasharray="2 3" />
              <text x={pad.l - 8} y={y + 3} textAnchor="end" fill="var(--muted)" fontSize="10" fontFamily="var(--font-mono)">
                {format(t)}
              </text>
            </g>
          );
        })}
        {data.map((d, i) => {
          const v = Number(d[valueKey]) || 0;
          const x = pad.l + i * bw + bw * 0.15;
          const h = (v / max) * ih;
          const y = pad.t + ih - h;
          const hw = bw * 0.7;
          const sv = secondaryKey ? Number(d[secondaryKey]) || 0 : 0;
          const sh = secondaryKey ? (sv / max) * ih : 0;
          return (
            <g key={i} onMouseEnter={() => setHoverI(i)}>
              <rect
                x={x}
                y={y}
                width={hw}
                height={h}
                rx="3"
                fill={hoverI === i ? "var(--accent-bright)" : "var(--accent)"}
                opacity={hoverI === null || hoverI === i ? 1 : 0.5}
              />
              {secondaryKey && (
                <rect x={x} y={y + h - sh} width={hw} height={sh} rx="3" fill="rgba(74,222,128,0.45)" />
              )}
              <text
                x={x + hw / 2}
                y={pad.t + ih + 15}
                textAnchor="middle"
                fontSize="10"
                fill="var(--muted)"
                fontFamily="var(--font-mono)"
              >
                {String(d[labelKey] ?? "")}
              </text>
            </g>
          );
        })}
      </svg>
      {hoverI != null && (
        <div
          style={{
            position: "absolute",
            left: Math.min(w - 170, pad.l + hoverI * bw + bw / 2),
            top: 6,
            background: "var(--bg-elev)",
            border: "1px solid var(--border-strong)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            pointerEvents: "none",
          }}
        >
          <div style={{ color: "var(--secondary)" }}>{String(data[hoverI][labelKey] ?? "")}</div>
          <div style={{ color: "var(--text)", fontWeight: 600, fontSize: 13 }}>
            {format(Number(data[hoverI][valueKey]) || 0)}
          </div>
          {secondaryKey && (
            <div style={{ color: "var(--success)" }}>
              saved {format(Number(data[hoverI][secondaryKey]) || 0)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------------- KPI ---------------- */
export interface TrendInfo {
  dir: "up" | "down" | "flat";
  label: string;
}

export function KPI({
  label,
  value,
  sub,
  accent,
  trend,
  spark,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  accent?: string;
  trend?: TrendInfo;
  spark?: number[];
}) {
  return (
    <div className="card-elev" style={{ padding: "14px 16px", display: "flex", gap: 12, minHeight: 102 }}>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div style={{ fontSize: 11, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--secondary)" }}>
            {label}
          </div>
          {trend && (
            <span
              className="mono"
              style={{
                fontSize: 11,
                color:
                  trend.dir === "up"
                    ? "var(--success)"
                    : trend.dir === "down"
                      ? "var(--error)"
                      : "var(--secondary)",
              }}
            >
              {trend.dir === "up" ? "▲" : trend.dir === "down" ? "▼" : "·"} {trend.label}
            </span>
          )}
        </div>
        <div
          className="mono"
          style={{ fontSize: 28, fontWeight: 600, lineHeight: 1.1, color: accent || "var(--text)" }}
        >
          {value}
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: "auto" }}>{sub}</div>
      </div>
      {spark && (
        <div style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
          <Sparkline scores={spark} width={116} height={46} />
        </div>
      )}
    </div>
  );
}

/* ---------------- Clipboard helper ---------------- */
// Shared copy logic: navigator.clipboard when available, textarea fallback for
// insecure origins (a dashboard reached over plain http on a LAN/VM) or old
// browsers. Returns [copied, copy] where `copied` flashes true for ~1.4s.
export function useClipboard(): [boolean, (value: string) => void] {
  const [copied, setCopied] = useState(false);
  const copy = (value: string) => {
    const done = () => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(value).then(done).catch(() => {});
    } else {
      try {
        const ta = document.createElement("textarea");
        ta.value = value;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        done();
      } catch { /* clipboard unavailable — no-op */ }
    }
  };
  return [copied, copy];
}

/* ---------------- CommandButton (compact click-to-copy pill) ---------------- */
export function CommandButton({ value, label }: { value: string; label?: string }) {
  const [copied, copy] = useClipboard();
  return (
    <button
      type="button"
      className="btn"
      onClick={() => copy(value)}
      title={`Copy: ${value}`}
    >
      <span aria-hidden style={{ color: "var(--accent)", fontWeight: 600 }}>+</span>
      <span>{copied ? "copied ✓" : (label ?? value)}</span>
    </button>
  );
}

/* ---------------- CopyField (click-to-copy command / snippet) ---------------- */
export function CopyField({
  value,
  display,
  mono = true,
}: {
  value: string;
  display?: ReactNode;
  mono?: boolean;
}) {
  const [copied, copy] = useClipboard();
  return (
    <button
      type="button"
      onClick={() => copy(value)}
      title="Copy to clipboard"
      className={mono ? "mono" : undefined}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        width: "100%",
        textAlign: "left",
        padding: "9px 11px",
        fontSize: 12.5,
        color: "var(--text-dim)",
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--r-md)",
        cursor: "pointer",
        transition: "border-color .12s ease",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--border-strong)")}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
    >
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {display ?? value}
      </span>
      <span
        style={{
          flexShrink: 0,
          fontSize: 10,
          fontFamily: "var(--font-ui)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: copied ? "var(--success)" : "var(--muted)",
        }}
      >
        {copied ? "copied ✓" : "copy"}
      </span>
    </button>
  );
}

/* ---------------- Wordmark ---------------- */
export function Wordmark() {
  return (
    <Link to="/" style={{ display: "flex", alignItems: "center", gap: 8, textDecoration: "none" }}>
      <svg
        viewBox="0 0 32 32"
        width="22"
        height="22"
        aria-hidden
        style={{ display: "block", flexShrink: 0 }}
      >
        <rect width="32" height="32" rx="7" fill="#1a1a1e" />
        <text
          x="0"
          y="27"
          fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif"
          fontWeight="700"
          fontSize="28"
          fill="#e8e8ec"
        >
          p
        </text>
        <text
          x="15"
          y="27"
          fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif"
          fontWeight="700"
          fontSize="28"
          fill="oklch(0.78 0.14 82)"
        >
          r
        </text>
      </svg>
      <span
        style={{
          fontFamily: "-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif",
          fontSize: 15,
          fontWeight: 700,
          color: "var(--text)",
          letterSpacing: "-0.015em",
        }}
      >
        prompt<span style={{ color: "var(--accent)" }}>ry</span>
      </span>
    </Link>
  );
}

/* ---------------- PageHeader ---------------- */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  tags,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  tags?: string[];
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-end",
        gap: 16,
        margin: "24px 0 20px",
      }}
    >
      <div style={{ minWidth: 0 }}>
        {eyebrow && (
          <div
            style={{
              fontSize: 11,
              color: "var(--muted)",
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              marginBottom: 6,
              fontFamily: "var(--font-mono)",
            }}
          >
            {eyebrow}
          </div>
        )}
        <h1
          className="enter"
          style={{
            margin: 0,
            fontSize: 26,
            fontWeight: 600,
            color: "var(--text)",
            letterSpacing: "-0.015em",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          {title}
          {tags &&
            tags.map((t) => (
              <span key={t} className="chip mono" style={{ fontSize: 10, textTransform: "uppercase" }}>
                {t}
              </span>
            ))}
        </h1>
        {description && (
          <div style={{ marginTop: 6, color: "var(--secondary)", fontSize: 12.5, maxWidth: 1040 }}>
            {description}
          </div>
        )}
      </div>
      {actions && <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>{actions}</div>}
    </div>
  );
}

/* ---------------- Breadcrumbs ---------------- */
export function Breadcrumbs({ items }: { items: { label: string; to?: string }[] }) {
  return (
    <div
      style={{
        fontSize: 12,
        color: "var(--muted)",
        marginTop: 20,
        fontFamily: "var(--font-mono)",
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      {items.map((it, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          {it.to ? (
            <Link
              to={it.to}
              style={{ color: "var(--accent)", textDecoration: "none" }}
              onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
              onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
            >
              {it.label}
            </Link>
          ) : (
            <span style={{ color: "var(--text-dim)" }}>{it.label}</span>
          )}
          {i < items.length - 1 && <span>/</span>}
        </span>
      ))}
    </div>
  );
}

/* ---------------- Select (themed dropdown) ---------------- */
export function Select<T extends string | number>({
  value,
  onChange,
  options,
  minWidth = 150,
  searchable = false,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  minWidth?: number;
  searchable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) { setQuery(""); return; }
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const current = options.find((o) => o.value === value);
  const shown = searchable && query
    ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase()))
    : options;

  return (
    <div ref={ref} style={{ position: "relative", minWidth }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          padding: "7px 11px",
          fontSize: 12.5,
          fontFamily: "var(--font-mono)",
          color: "var(--text)",
          background: "var(--bg-elev)",
          border: `1px solid ${open ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 8,
          cursor: "pointer",
          transition: "border-color 0.15s",
        }}
      >
        <span>{current?.label ?? ""}</span>
        <span
          style={{
            color: "var(--muted)",
            fontSize: 10,
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 0.15s",
          }}
        >
          ▾
        </span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            zIndex: 50,
            padding: 4,
            background: "var(--bg-elev)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            boxShadow: "0 8px 28px -6px rgba(0,0,0,0.6)",
          }}
        >
          {searchable && (
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search…"
              style={{
                width: "100%",
                boxSizing: "border-box",
                padding: "6px 9px",
                marginBottom: 4,
                fontSize: 12,
                fontFamily: "var(--font-mono)",
                color: "var(--text)",
                background: "var(--bg)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                outline: "none",
              }}
            />
          )}
          <div style={{ maxHeight: 260, overflowY: "auto" }}>
            {shown.length === 0 && (
              <div style={{ padding: "8px 10px", fontSize: 11.5, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                no matches
              </div>
            )}
            {shown.map((o) => {
              const active = o.value === value;
              return (
                <button
                  key={String(o.value)}
                  type="button"
                  onClick={() => {
                    onChange(o.value);
                    setOpen(false);
                  }}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    padding: "7px 10px",
                    fontSize: 12.5,
                    fontFamily: "var(--font-mono)",
                    color: active ? "var(--bg)" : "var(--text-dim)",
                    background: active ? "var(--accent)" : "transparent",
                    border: "none",
                    borderRadius: 6,
                    cursor: "pointer",
                  }}
                  onMouseEnter={(e) => {
                    if (!active) e.currentTarget.style.background = "rgba(255,255,255,0.05)";
                  }}
                  onMouseLeave={(e) => {
                    if (!active) e.currentTarget.style.background = "transparent";
                  }}
                >
                  {o.label}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
