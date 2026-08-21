// A short walkthrough of the dashboard, built on driver.js.
//
// - Demo build: runs on every page load, so any visitor sees it.
// - Real dashboard: runs once per browser (first sign-in), then stays quiet.
// Replayable anytime from the sidebar "Take a tour" button.
//
// Eight steps, grouped by area rather than one-per-menu. Anchored to [data-tour]
// elements and filtered to what's on the page, so it's safe on any route / role.
import { driver, type DriveStep } from "driver.js";
import "driver.js/dist/driver.css";

const SEEN_KEY = "promptry-tour-v4";
const DEMO = import.meta.env.VITE_DEMO === "1";

// Auto-run at most once per page load (so moving around doesn't relaunch it).
let autoStarted = false;

function buildSteps(): DriveStep[] {
  const steps: DriveStep[] = [
    {
      element: '[data-tour="brand"]',
      popover: {
        title: "promptry",
        description:
          "A local-first dashboard for your LLM calls — prompt versions, eval results, cost, and traces, all backed by one SQLite file. Quick orientation:",
        side: "right",
        align: "start",
      },
    },
    {
      // Highlight the whole content area, not just the nav item, so this one step
      // explains the Overview tab *and* what it actually shows.
      element: '[data-tour="content"]',
      popover: {
        title: "Overview",
        description:
          "The landing view: suite pass/fail, spend, drift, and recent end-user feedback — the health of your prompts at a glance.",
        side: "left",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-build"]',
      popover: {
        title: "Build",
        description: "Version prompts, run eval suites against them, and collapse near-duplicate templates.",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-measure"]',
      popover: {
        title: "Measure",
        description:
          "Per-call cost by prompt and model, model-vs-model comparisons, and per-step trace waterfalls.",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-signals"]',
      popover: {
        title: "Signals",
        description: "End-user ratings, and a playground for running prompts against live models.",
        side: "right",
        align: "start",
      },
    },
    {
      element: '[data-tour="grp-setup"]',
      popover: {
        title: "Setup",
        description:
          "Budget caps, alert routing (Slack / PagerDuty / webhook), and — with auth enabled — users and an audit log.",
        side: "right",
        align: "start",
      },
    },
    {
      popover: {
        title: "Instrument in one line",
        description:
          "Point your client at promptry — <code>from promptry.openai import OpenAI</code> (or call <code>track_invocation()</code>) — and every call shows up here. Replay this with <b>Take a tour</b> in the sidebar.",
      },
    },
  ];
  // Keep only steps whose anchor is on the page (element-less wrap step always
  // stays), so the tour never points at something that isn't there.
  return steps.filter((s) => !s.element || document.querySelector(s.element as string));
}

/** Start the tour immediately. */
export function startTour(): void {
  const steps = buildSteps();

  // Enter always advances (and finishes on the last step), regardless of which
  // control driver happened to focus. Capture phase so it beats a button's own
  // Enter handling.
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Enter" || !document.querySelector(".driver-popover")) return;
    e.preventDefault();
    e.stopPropagation();
    const i = obj.getActiveIndex();
    if (i !== undefined && i >= steps.length - 1) obj.destroy();
    else obj.moveNext();
  };

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
    doneBtnText: "Done",
    steps,
    onPopoverRender: (popover) => {
      // "Skip" as a non-focusable span so driver's autofocus can't land on it
      // (a real button would grab focus and Enter would exit the tour).
      const footer = popover.footerButtons.parentElement;
      if (footer && !footer.querySelector(".pt-skip")) {
        const skip = document.createElement("span");
        skip.className = "pt-skip";
        skip.setAttribute("role", "button");
        skip.textContent = "Skip";
        skip.addEventListener("click", () => obj.destroy());
        footer.insertBefore(skip, footer.firstChild);
      }
      // Put the visible focus ring on Next (the default action).
      setTimeout(() => popover.nextButton?.focus(), 60);
    },
    onDestroyed: () => {
      document.removeEventListener("keydown", onKey, true);
      markSeen();
    },
  });

  document.addEventListener("keydown", onKey, true);
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
