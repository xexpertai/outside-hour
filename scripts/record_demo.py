"""Record a raw demo: a real run of the web app against the real local model, at phone (or desktop) size.

Needs: llama-server running (scripts/start-model.sh), the web app on :8765 (`python3 -m outside_hour serve`) and
Playwright (dev only: `uv pip install playwright`). Nothing here fakes a result: the page talks to the real server
and the real model. The recording is clean (no captions); `timeline-<tag>.json` lists when each thing happens so an
editor can caption and cut it, and `plan-<tag>.json` is the exact plan the model wrote during the recording.

Usage:
  python scripts/record_demo.py --viewport phone --preset mg-road --minutes 60 --start 07:00 --tag phone-mg-road
  python scripts/record_demo.py --viewport desktop --preset lalbagh --minutes 45 --start 17:30 --tag desktop-lalbagh
  add --shots demo/shots-v2 to also save full-resolution PNGs (form, card, phone down, print card) from the same plan.

"Time away" on the phone-down screen: headless Chromium never hides a page, so the script SIMULATES a phone lock
for --away seconds by telling the page it is hidden (see SET_HIDDEN). The timeline marks this as simulated.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765/"
VIEWPORTS = {
    "phone": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True},
    "desktop": {"viewport": {"width": 1280, "height": 800}, "device_scale_factor": 1},
}
VIDEO_SIZE = {"phone": {"width": 780, "height": 1688}, "desktop": {"width": 1280, "height": 800}}
# Playwright records video in CSS pixels, so a 390-wide phone would be a 390-pixel video. For a sharp phone video
# the page is laid out at 390 x 844 CSS px and drawn at 2x with CSS zoom inside a 780 x 1688 window.
VIDEO_CONTEXT = {
    "phone": {"viewport": {"width": 780, "height": 1688}, "device_scale_factor": 1, "has_touch": True},
    "desktop": VIEWPORTS["desktop"],
}
ZOOM = {"phone": 2, "desktop": 1}
PRESET_LABEL = {"mg-road": "MG Road metro station, Bengaluru", "lalbagh": "Lalbagh main gate, Bengaluru",
                "pune-empress": "Empress Garden, Pune"}


# Headless Chromium keeps every page "visible", so a real phone lock can't be reproduced here. This stands in for
# it: the page is told it is hidden (exactly what a phone browser reports when the screen locks) and the app's
# own visibility code does the measuring. Captions must say the lock is simulated.
SET_HIDDEN = """(hidden) => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => hidden ? 'hidden' : 'visible'});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
  document.dispatchEvent(new Event('visibilitychange'));
}"""


def smooth_scroll(page, total, steps=10, pause=0.12):
    for _ in range(steps):
        page.mouse.wheel(0, total / steps)
        time.sleep(pause)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewport", choices=sorted(VIEWPORTS), default="phone")
    ap.add_argument("--preset", choices=sorted(PRESET_LABEL), default="mg-road")
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--start", default="07:00")
    ap.add_argument("--away", type=float, default=20.0, help="seconds the app tab is hidden after Phone down")
    ap.add_argument("--out", default="demo/raw-v2")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--shots", default=None, help="folder for full-resolution PNG screenshots of the same plan")
    ap.add_argument("--scheme", choices=["light", "dark"], default="light")
    a = ap.parse_args()
    tag = a.tag or f"{a.viewport}-{a.preset}"
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    ev = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(record_video_dir=str(out), record_video_size=VIDEO_SIZE[a.viewport],
                                  color_scheme=a.scheme, **VIDEO_CONTEXT[a.viewport])
        if ZOOM[a.viewport] != 1:
            ctx.add_init_script(f"document.addEventListener('DOMContentLoaded', () => "
                                f"{{ document.documentElement.style.zoom = '{ZOOM[a.viewport]}'; }})")
        page = ctx.new_page()
        v0 = time.time()
        mark = lambda name, **kw: ev.append(dict(t=round(time.time() - v0, 2), event=name, **kw))  # noqa: E731
        page.goto(URL)
        page.wait_for_selector("#presets button")
        mark("form_shown", note="budget ring starts at 60 s")
        time.sleep(3)
        page.get_by_role("button", name=PRESET_LABEL[a.preset], exact=True).click()
        mark("preset_tapped", preset=a.preset)
        time.sleep(1.5)
        page.click(f"#mins button[data-v='{a.minutes}']")
        mark("minutes_tapped", minutes=a.minutes)
        time.sleep(1)
        if a.start:
            page.fill("#start", a.start)
            page.dispatch_event("#start", "input")
            mark("start_time_set", start=a.start)
        time.sleep(1.5)
        t0 = time.time()
        page.click("#go")
        mark("plan_requested")
        page.evaluate("document.getElementById('go').scrollIntoView({block: 'start', behavior: 'smooth'})")
        page.wait_for_selector("#card", timeout=240000)
        took = time.time() - t0
        mark("card_shown", plan_seconds=round(took, 1))
        print(f"plan took {took:.1f}s", file=sys.stderr)
        plan = page.evaluate("plan")
        time.sleep(4)
        mark("scroll_card_start")
        height = page.evaluate("document.getElementById('card').getBoundingClientRect().height")
        for _ in range(4):
            smooth_scroll(page, min(500, height / 4))
            time.sleep(2.5)
        mark("scroll_card_end")
        page.evaluate("document.getElementById('b-down').scrollIntoView({block: 'center', behavior: 'smooth'})")
        # The nudge appears on its own once the 60-second budget is spent (the model wait counts: the page was visible).
        try:
            page.wait_for_selector("#nudge.on", timeout=60000)
            mark("budget_over_nudge_shown")
        except Exception:
            mark("budget_not_over_yet")
        time.sleep(4)
        page.click("#b-down")
        mark("phone_down")
        time.sleep(5)
        page.evaluate(SET_HIDDEN, True)
        mark("page_hidden_SIMULATED", seconds=a.away)
        time.sleep(a.away)
        page.evaluate(SET_HIDDEN, False)
        mark("page_visible_again")
        time.sleep(7)
        away_txt = page.inner_text("#d-away")
        screen_txt = page.inner_text("#d-screen")
        mark("phone_down_readout", on_screen=screen_txt, away=away_txt)
        if a.shots:  # the phone-down still comes from the real run, so its two meters are the run's own numbers
            (ROOT / a.shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ROOT / a.shots / "3-phone-down.png"))
        time.sleep(2)
        ctx.close()
        video = Path(page.video.path())
        final = out / f"{tag}.webm"
        video.rename(final)

        if a.shots:
            shots(browser, ROOT / a.shots, plan, a)
        browser.close()

    timeline = {
        "tag": tag, "viewport": a.viewport, "video": final.name, "video_size": VIDEO_SIZE[a.viewport],
        "layout_css_px": VIEWPORTS[a.viewport]["viewport"], "css_zoom": ZOOM[a.viewport], "color_scheme": a.scheme,
        "preset": a.preset, "minutes": a.minutes, "start": a.start,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "writer": plan.get("writer"), "place": plan["place"]["name"], "plan_seconds": round(took, 1),
        "model_stats": plan.get("model_stats"), "rejected": plan.get("rejected"),
        "away_note": ("SIMULATED: headless Chromium always reports pages as visible, so the script overrides "
                      "document.visibilityState to 'hidden' and fires visibilitychange for the 'away' seconds, as "
                      "a phone browser does when the screen is locked. The app's own code then measures it."),
        "events": ev,
    }
    (out / f"timeline-{tag}.json").write_text(json.dumps(timeline, indent=2, ensure_ascii=False))
    (out / f"plan-{tag}.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    print(json.dumps(timeline, indent=2, ensure_ascii=False), file=sys.stderr)


def shots(browser, folder: Path, plan: dict, a):
    """Full-resolution stills from the same real plan (rendered with the page's own render function)."""
    folder.mkdir(parents=True, exist_ok=True)
    ctx = browser.new_context(color_scheme=a.scheme, **VIEWPORTS["phone"])
    pg = ctx.new_page()
    pg.goto(URL)
    pg.wait_for_selector("#presets button")
    pg.get_by_role("button", name=PRESET_LABEL[a.preset], exact=True).click()
    pg.click(f"#mins button[data-v='{a.minutes}']")
    if a.start:
        pg.fill("#start", a.start)
        pg.dispatch_event("#start", "input")
    pg.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
    time.sleep(0.5)
    pg.screenshot(path=str(folder / "1-form.png"))
    pg.evaluate("(p) => { plan = p; render(p); }", plan)
    pg.wait_for_selector("#card")
    pg.evaluate("document.getElementById('card').scrollIntoView({block: 'start'})")
    time.sleep(0.8)
    pg.screenshot(path=str(folder / "2-card.png"))
    pg.screenshot(path=str(folder / "2-card-full.png"), full_page=True)
    pg.emulate_media(media="print")
    pg.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.5)
    box = pg.locator("#card").bounding_box()
    pg.screenshot(path=str(folder / "4-print-card.png"), full_page=True,
                  clip={"x": 0, "y": 0, "width": box["x"] + box["width"] + 2, "height": box["y"] + box["height"] + 2})
    ctx.close()


if __name__ == "__main__":
    main()
