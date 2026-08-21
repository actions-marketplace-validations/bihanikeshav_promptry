// A short, friendly walkthrough of the dashboard, built on driver.js.
//
// - Demo build: runs on every page load, so any visitor sees it.
// - Real dashboard: runs once per browser (first sign-in), then stays quiet.
// Replayable anytime from the sidebar "Take a tour" button.
//
// Eight steps, grouped by area rather than one-per-menu. Anchored to [data-tour]
// elements and filtered to what's on the page, so it's safe on any route / role.
import { driver, type DriveStep } from "driver.js";
import "driver.js/dist/driver.css";

const SEEN_KEY = "promptry-tour-v3";
const DEMO = import.meta.env.VITE_DEMO === "1";

// Auto-run at most once per page load (so moving around doesn't relaunch it).
let autoStarted = false;

function buildSteps(): DriveStep[] {
  const steps: DriveStep[] = [
    {
      element: '[data-tour="brand"]',
      popover: {
        title: "Welcome 👋",
        description:
          "This is <b>promptry</b> — your dashboard for prompts, cost, and results. Here's a quick look around (about 20 seconds).",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="search"]',
      popover: {
        title: "Find anything fast",
        description: "Press <span class=\"pt-kbd\">⌘K</span> any time to jump to a suite, a prompt, or a page.",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-home"]',
      popover: {
        title: "Overview",
        description: "Your home base — a quick read on how your prompts are doing.",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-build"]',
      popover: {
        title: "Build",
        description: "Version your prompts, test them for regressions, and trim near-duplicates.",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-measure"]',
      popover: {
        title: "Measure",
        description:
          "What you're spending, how models compare, and where each step of a run goes.",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-signals"]',
      popover: {
        title: "Signals",
        description: "Hear from your users, and try prompts out against a live model.",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-setup"]',
      popover: {
        title: "Setup",
        description: "Budgets, alerts, and — on a team — users and an audit log.",
        side: "right",
        align: "start",
      },
    },
    {
      popover: {
        title: "That's the tour!",
        description:
          "Add one line to your app — <code>from promptry.openai import OpenAI</code> — and everything here fills in on its own. Replay anytime with <b>Take a tour</b> in the sidebar.",
      },
    },
  ];
  // Keep only steps whose anchor is on the page (the element-less wrap step
  // always stays), so the tour never points at something that isn't there.
  return steps.filter((s) => !s.element || document.querySelector(s.element as string));
}

/** Start the tour immediately. */
export function startTour(): void {
  const obj = driver({
    showProgress: true,
    allowClose: true, // Esc still exits
    // Click anywhere on the backdrop to move forward; the spotlighted item stays
    // non-interactive so a stray click can't navigate mid-tour.
    overlayClickBehavior: "nextStep",
    disableActiveInteraction: true,
    overlayColor: "rgba(0,0,0,0.55)",
    stagePadding: 6,
    stageRadius: 8,
    popoverClass: "promptry-tour",
    nextBtnText: "Next",
    prevBtnText: "Back",
    doneBtnText: "Got it",
    steps: buildSteps(),
    onPopoverRender: (popover) => {
      // Add a plain "Skip" control on the left of the footer (once per render).
      const footer = popover.footerButtons.parentElement;
      if (!footer || footer.querySelector(".pt-skip")) return;
      const skip = document.createElement("button");
      skip.type = "button";
      skip.textContent = "Skip";
      skip.className = "pt-skip";
      skip.addEventListener("click", () => obj.destroy());
      footer.insertBefore(skip, footer.firstChild);
    },
    onDestroyed: markSeen,
  });
  obj.drive();
}

/**
 * Auto-run for a newcomer. Demo: every page load. Real dashboard: once per
 * browser. Call once the landing route has mounted so anchors exist.
 */
export function maybeAutoTour(): void {
  if (autoStarted) return;
  if (!DEMO && hasSeen()) return;
  autoStarted = true;
  window.setTimeout(() => {
    if (document.querySelector('[data-tour="brand"]')) startTour();
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
