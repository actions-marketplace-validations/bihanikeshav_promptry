import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, HashRouter } from "react-router-dom";
import App from "./App";
import AuthGate from "./components/AuthGate";
import "./styles.css";

// In a demo build (VITE_DEMO), install the mock-fetch layer before render and
// use hash routing so the static GitHub Pages deploy works without a backend
// or server-side SPA rewrites. The mock module is dynamically imported so it
// never ships in the real dashboard bundle.
const DEMO = import.meta.env.VITE_DEMO === "1";

async function boot() {
  if (DEMO) {
    const { installDemoFetch } = await import("./demo/mock");
    installDemoFetch();
  }
  const Router = DEMO ? HashRouter : BrowserRouter;
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <Router>
        {DEMO ? (
          <App />
        ) : (
          <AuthGate>
            <App />
          </AuthGate>
        )}
      </Router>
    </React.StrictMode>
  );
}

boot();
