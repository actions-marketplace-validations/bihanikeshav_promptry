import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { SideNav } from "./SideNav";
import { ActivityFooter } from "./ActivityFooter";
import { CommandK } from "./CommandK";
import { getSuites, getPrompts } from "../api/client";
import type { SuiteSummary, PromptSummary } from "../api/types";

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

  useEffect(() => {
    getSuites().then(setSuites).catch(() => setSuites([]));
    getPrompts().then(setPrompts).catch(() => setPrompts([]));
  }, [nonce]);

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
  const isOverview = useLocation().pathname === "/";

  const ctx: LayoutContext = {
    suites,
    prompts,
    refresh: () => setNonce((n) => n + 1),
  };

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "row", overflow: "hidden" }}>
      <SideNav
        onCmdK={() => setCmdOpen(true)}
        counters={{ suites: suites.length, regressions, drifting }}
      />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>
        <main
          style={{
            flex: 1,
            minHeight: 0,
            maxWidth: 1400,
            width: "100%",
            margin: "0 auto",
            padding: isOverview ? "4px 32px 8px" : "4px 32px 40px",
            // Overview fits the viewport (drops content instead of scrolling);
            // every other page scrolls inside main, so the document never does.
            overflowY: isOverview ? "hidden" : "auto",
          }}
        >
          <Outlet context={ctx} />
        </main>
        {isOverview && <ActivityFooter suites={suites} />}
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
