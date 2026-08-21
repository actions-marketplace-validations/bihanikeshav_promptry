import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { SideNav } from "./SideNav";
import { CommandK } from "./CommandK";
import { getSuites, getPrompts } from "../api/client";
import type { SuiteSummary, PromptSummary } from "../api/types";
import { maybeAutoTour } from "../tour";

export interface LayoutContext {
  suites: SuiteSummary[];
  prompts: PromptSummary[];
  refresh: () => void;
}

export default function Layout() {
  const [suites, setSuites] = useState<SuiteSummary[]>([]);
  const [prompts, setPrompts] = useState<PromptSummary[]>([]);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [nonce, setNonce] = useState(0);
  const location = useLocation();

  useEffect(() => {
    getSuites().then(setSuites).catch(() => setSuites([]));
    getPrompts().then(setPrompts).catch(() => setPrompts([]));
  }, [nonce]);

  // First-visit walkthrough: fire once the landing (Overview) route is mounted,
  // so the tour's content + sidebar anchors exist. Guards on localStorage inside.
  useEffect(() => {
    if (location.pathname === "/") maybeAutoTour();
  }, [location.pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const regressions = suites.filter((s) => !s.passed).length;
  const drifting = suites.filter((s) => s.drift_status === "drifting").length;

  const ctx: LayoutContext = {
    suites,
    prompts,
    refresh: () => setNonce((n) => n + 1),
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "row" }}>
      <SideNav
        onCmdK={() => setCmdOpen(true)}
        counters={{ suites: suites.length, regressions, drifting }}
      />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <main
          data-tour="content"
          style={{
            flex: 1,
            maxWidth: 1400,
            width: "100%",
            margin: "0 auto",
            padding: "4px 32px 40px",
          }}
        >
          <Outlet context={ctx} />
        </main>
      </div>
      <CommandK
        open={cmdOpen}
        onClose={() => setCmdOpen(false)}
        suites={suites}
        prompts={prompts}
      />
    </div>
  );
}
