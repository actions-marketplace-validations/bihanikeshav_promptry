// A light, friendly walkthrough of the dashboard, built on driver.js.
//
// - Demo build: runs on every page load, so any visitor sees it.
// - Real dashboard: runs once per browser (first sign-in), then stays quiet.
// Either way it's replayable from the sidebar "Take a tour" button.
//
// Steps target [data-tour="…"] anchors and are filtered to whatever's actually
// on the page, so admin-only items (Users, Audit) simply drop out when absent.
import { driver, type DriveStep } from "driver.js";
import "driver.js/dist/driver.css";

const SEEN_KEY = "promptry-tour-v2";
const DEMO = import.meta.env.VITE_DEMO === "1";

// Only auto-run once per page load (so moving around the demo doesn't relaunch it).
let autoStarted = false;

function navStep(to: string, title: string, description: string): DriveStep {
  return {
    element: `[data-tour="nav-${to}"]`,
    popover: { title, description, side: "right", align: "start" },
  };
}

function buildSteps(): DriveStep[] {
  const steps: DriveStep[] = [
    {
      popover: {
        title: "Welcome 👋",
        description:
          "This is your promptry dashboard — where your prompts, costs, and results all show up. Here's a quick look around (about 20 seconds).",
      },
    },
    {
      element: '[data-tour="search"]',
      popover: {
        title: "Find anything fast",
        description:
          "Press <span class=\"pt-kbd\">⌘K</span> any time to jump straight to a suite, a prompt, or a page.",
        side: "right",
        align: "start",
      },
    },
    navStep("/", "Overview", "Your home base — a quick read on how your prompts are doing."),
    navStep("/evals", "Evals", "Your tests. See what's passing and what slipped, run to run."),
    navStep("/prompts", "Prompts", "Every prompt you've used, kept with its full history."),
    navStep("/cache", "Cache optimization", "Spots near-duplicate prompts and easy ways to save tokens."),
    navStep("/models", "Models", "Compare how different models do on the same tests."),
    navStep("/cost", "Cost", "What you're spending, broken down by prompt and model."),
    navStep("/traces", "Call traces", "Follow a multi-step run and see what each step costs."),
    navStep("/feedback", "Feedback", "Thumbs-up and thumbs-down from your users, in one place."),
    navStep("/playground", "Playground", "Try a prompt against a live model right here."),
    navStep("/settings", "Settings", "Budgets, alerts, and the rest of your setup."),
    navStep("/users", "Users", "Invite teammates and set who can do what."),
    navStep("/audit", "Audit log", "A record of who changed what, and when."),
    {
      element: '[data-tour="stats"]',
      popover: {
        title: "Today, at a glance",
        description: "A running tally of today's spend, calls, and tokens.",
        side: "right",
        align: "end",
      },
    },
    {
      popover: {
        title: "That's the tour!",
        description:
          "Add one line to your app — <code>from promptry.openai import OpenAI</code> — and everything here fills in on its own. You can replay this anytime with <b>Take a tour</b> in the sidebar.",
      },
    },
  ];
  // Keep only steps whose anchor is on the page (the element-less welcome/wrap
  // steps always stay), so the tour never points at something that isn't there.
  return steps.filter((s) => !s.element || document.querySelector(s.element as string));
}

/** Start the tour immediately. */
export function startTour(): void {
  driver({
    showProgress: true,
    allowClose: true,
    overlayColor: "rgba(0,0,0,0.55)",
    stagePadding: 6,
    stageRadius: 8,
    popoverClass: "promptry-tour",
    nextBtnText: "Next",
    prevBtnText: "Back",
    doneBtnText: "Got it",
    steps: buildSteps(),
    onDestroyed: markSeen,
  }).drive();
}

/**
 * Auto-run the tour for a newcomer. Demo: every page load. Real dashboard: once
 * per browser. Call it once the landing route has mounted so anchors exist.
 */
export function maybeAutoTour(): void {
  if (autoStarted) return;
  if (!DEMO && hasSeen()) return;
  autoStarted = true;
  window.setTimeout(() => {
    if (document.querySelector('[data-tour="search"]')) startTour();
  }, DEMO ? 450 : 650);
}

function hasSeen(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    // No storage (private mode): treat as seen so we never nag on every load.
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
