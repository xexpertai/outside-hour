/* Attention budget: how long this page has been on screen, and how that compares with a 60-second budget.
 *
 * Honest by design: it counts only time while the page is visible (Page Visibility API). It cannot see
 * other apps, and it is not a measure of "screen time" for the whole phone. After "Phone down" a second
 * meter counts the time the page is hidden, which is the closest a web page can get to "time away".
 *
 * Pure functions with injected clocks, so tests can run them in Node (tests/test_attention_js.py).
 */
(function (root) {
  "use strict";
  const BUDGET_S = 60;

  /* A stopwatch that runs only while the page is visible (mode "visible") or hidden (mode "hidden"). */
  function createMeter(now, pageVisible, mode) {
    const countWhen = mode === "hidden" ? false : true;
    let total = 0;
    let since = pageVisible === countWhen ? now : null;
    let stopped = false;
    return {
      setVisible(visible, t) {
        if (stopped) return;
        const running = since !== null;
        const shouldRun = visible === countWhen;
        if (running && !shouldRun) { total += t - since; since = null; }
        else if (!running && shouldRun) { since = t; }
      },
      ms(t) { return total + (since !== null ? Math.max(0, t - since) : 0); },
      stop(t) { if (!stopped) { total = this.ms(t); since = null; stopped = true; } },
      resume(t, visible) { if (stopped) { stopped = false; since = visible === countWhen ? t : null; } },
      get stopped() { return stopped; },
    };
  }

  /* What the UI should show for a given amount of on-screen time. */
  function budgetState(spentMs, budgetS) {
    const b = budgetS || BUDGET_S;
    const s = Math.max(0, spentMs) / 1000;
    const warnAt = b * 2 / 3;
    let phase = "ok";
    if (s >= b) phase = "over";
    else if (s >= warnAt) phase = "low";
    // The page dims gently from 2/3 of the budget (0) to the end of it (0.25), then slowly to at most 0.45.
    let dim = 0;
    if (s >= warnAt) dim = Math.min(0.25, ((s - warnAt) / (b - warnAt)) * 0.25);
    if (s > b) dim = Math.min(0.45, 0.25 + ((s - b) / b) * 0.2);
    return {
      spentS: Math.floor(s),
      leftS: Math.max(0, Math.ceil(b - s)),
      overS: Math.max(0, Math.floor(s - b)),
      fraction: Math.min(1, s / b),
      phase,
      dim: Math.round(dim * 1000) / 1000,
    };
  }

  function fmt(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(r).padStart(2, "0");
  }

  /* One sentence comparing the two meters for the phone-down screen. */
  function compareLine(screenMs, awayMs) {
    if (awayMs < 1000) return "Away from this screen: not yet. Lock the phone and go.";
    const ratio = awayMs / Math.max(1000, screenMs);
    if (ratio >= 2) return "For every minute on this screen, about " + Math.floor(ratio) + " away from it.";
    return "Away from this screen for " + fmt(awayMs) + " so far.";
  }

  const api = { BUDGET_S, createMeter, budgetState, fmt, compareLine };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Attention = api;
})(typeof self !== "undefined" ? self : this);
