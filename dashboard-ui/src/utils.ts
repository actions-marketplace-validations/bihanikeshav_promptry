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

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
