// A lightweight guided tour of the dashboard, built on driver.js. Shown once to
// a first-time visitor (real dashboard: first sign-in; demo: first page view),
// and replayable any time from the sidebar "Take a tour" button.
//
// Steps target [data-tour="…"] anchors so they don't break when styles change.
// Missing anchors are filtered out, so the tour is safe on any route.
import { driver, type DriveStep } from "driver.js";
import "driver.js/dist/driver.css";

const SEEN_KEY = "promptry-tour-v1";

function buildSteps(): DriveStep[] {
  const steps: DriveStep[] = [
    {
      element: '[data-tour="brand"]',
      popover: {
        title: "Welcome to promptry",
        description:
          "A local-first dashboard for your prompts — evals, cost, drift, and call traces, all from one SQLite file. Here's the 30-second tour.",
      },
    },
    {
      element: '[data-tour="search"]',
      popover: {
        title: "Jump anywhere",
        description:
          "Press <span class=\"pt-kbd\">⌘K</span> (or <span class=\"pt-kbd\">Ctrl-K</span>) to search suites, prompts, and pages from any screen.",
      },
    },
    {
      element: '[data-tour="nav"]',
      popover: {
        title: "The whole workspace",
        description:
          "<b>Evals</b> catch regressions before they ship · <b>Prompts</b> version every template · <b>Cost</b> breaks spend down to the call · <b>Call traces</b> show a per-step token/$ waterfall.",
      },
    },
    {
      element: '[data-tour="content"]',
      popover: {
        title: "Your workspace at a glance",
        description:
          "Suite health, spend, and drift on one screen. The numbers fill in as your app records calls.",
      },
    },
    {
      element: '[data-tour="stats"]',
      popover: {
        title: "Today, live",
        description:
          "Spend, calls, and tokens for the current day — read straight from the invocations ledger.",
      },
    },
    {
      popover: {
        title: "That's it — wire it up in one line",
        description:
          "Swap your import for <code>from promptry.openai import OpenAI</code> and every call lands here. Replay this tour anytime from <b>Take a tour</b> in the sidebar.",
      },
    },
  ];
  // Keep only steps whose anchor is actually on the page (the final, element-less
  // step always stays), so the tour never points at something that isn't there.
  return steps.filter((s) => !s.element || document.querySelector(s.element as string));
}

/** Start the tour immediately. */
export function startTour(): void {
  const d = driver({
    showProgress: true,
    allowClose: true,
    overlayColor: "rgba(0,0,0,0.55)",
    stagePadding: 6,
    stageRadius: 8,
    popoverClass: "promptry-tour",
    nextBtnText: "Next →",
    prevBtnText: "← Back",
    doneBtnText: "Done",
    steps: buildSteps(),
    onDestroyed: markSeen,
  });
  d.drive();
}

/** Start the tour once — first visit only. Call after the layout has rendered. */
export function maybeAutoTour(): void {
  if (hasSeen()) return;
  // Give the sidebar + content a beat to mount so anchors exist.
  window.setTimeout(() => {
    if (!hasSeen() && document.querySelector('[data-tour="brand"]')) startTour();
  }, 650);
}

function hasSeen(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    // No storage (private mode): treat as "seen" so we never nag on every load.
    return true;
  }
}

function markSeen(): void {
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch {
    /* ignore */
  }
}
