export function cls(...args: (string | false | null | undefined)[]): string {
  return args.filter(Boolean).join(" ");
}

export function pct(n: number | null | undefined, digits = 0): string {
  if (n == null) return "—";
  return (n * 100).toFixed(digits) + "%";
}

export function usd(n: number | null | undefined, digits = 2): string {
  if (n == null) return "—";
  return "$" + n.toFixed(digits);
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 60) return s + "s ago";
  const m = Math.round(s / 60);
  if (m < 60) return m + "m ago";
  const h = Math.round(m / 60);
  if (h < 24) return h + "h ago";
  const day = Math.round(h / 24);
  return day + "d ago";
}

export function scoreColor(s: number | null | undefined): string {
  if (s == null) return "var(--muted)";
  if (s >= 0.85) return "var(--success)";
  if (s >= 0.7) return "var(--warning)";
  return "var(--error)";
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}

// Template variables: preferred {{name}} plus legacy $name / ${name}.
// Global flag for use with matchAll / split; build fresh instances where a
// stateful lastIndex would bite.
export const VAR_RE = /\{\{\s*([A-Za-z_]\w*)\s*\}\}|\$\{[A-Za-z_]\w*\}|\$[A-Za-z_]\w*/g;

/** The distinct variable names a template references, in first-seen order. */
export function templateVars(text: string): string[] {
  const seen: string[] = [];
  for (const m of text.matchAll(VAR_RE)) {
    const tok = m[0];
    const name = m[1] ?? (tok.startsWith("${") ? tok.slice(2, -1) : tok.slice(1));
    if (!seen.includes(name)) seen.push(name);
  }
  return seen;
}

/** Split a template into literal/variable segments for highlighted rendering. */
export function splitTemplate(text: string): { text: string; isVar: boolean }[] {
  const out: { text: string; isVar: boolean }[] = [];
  let last = 0;
  const re = new RegExp(VAR_RE.source, "g");
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), isVar: false });
    out.push({ text: m[0], isVar: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last), isVar: false });
  return out;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
