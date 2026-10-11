"""Record the demo video: a real run of the web app against the real local model.

Needs: llama-server running (scripts/start-model.sh), the web app running on :8765, and Playwright
(dev only: `uv pip install playwright`). Output: demo/raw/*.webm, then scripts/make-demo-media.sh.
Nothing here fakes a result: the page talks to the real server and the real model; captions are
added on top of the page and say what is happening.
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "raw"
URL = "http://127.0.0.1:8765/"
W, H = 1280, 720

CAPTION_JS = """
(text) => {
  let c = document.getElementById('__cap');
  if (!c) {
    c = document.createElement('div'); c.id = '__cap';
    Object.assign(c.style, {position:'fixed', left:'0', right:'0', bottom:'0', padding:'12px 20px',
      background:'rgba(10,20,12,0.88)', color:'#fff', font:'600 19px system-ui, sans-serif',
      textAlign:'center', zIndex: 99999, lineHeight: '1.35'});
    document.body.appendChild(c);
  }
  c.innerHTML = text;
}
"""

BADGE_JS = """
() => {
  const b = document.createElement('div');
  Object.assign(b.style, {position:'fixed', left:'14px', top:'12px', padding:'6px 12px', borderRadius:'8px',
    background:'#2f6b3a', color:'#fff', font:'600 14px system-ui, sans-serif', zIndex: 99999});
  b.textContent = 'Real run · local Gemma 4 E2B on a laptop CPU (no GPU)';
  document.body.appendChild(b);
}
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT),
                                  record_video_size={"width": W, "height": H}, device_scale_factor=1)
        page = ctx.new_page()
        v0 = time.time()
        page.goto(URL)
        page.wait_for_selector("#presets button")
        page.evaluate(BADGE_JS)
        # Recording only: room for the badge at the top and the caption bar at the bottom.
        page.add_style_tag(content="main { padding-top: 58px !important; padding-bottom: 90px !important; }")
        cap = lambda t: page.evaluate(CAPTION_JS, t)  # noqa: E731
        cap("Outside Hour: one real plan to get outside, then put the phone away.")
        time.sleep(3.5)
        cap("Start: MG Road metro station, Bengaluru (a public place, used as the demo start)")
        page.get_by_role("button", name="MG Road metro station, Bengaluru").click()
        time.sleep(2)
        cap("I have 60 minutes, tomorrow at 07:00")
        page.click("#mins button[data-v='60']")
        time.sleep(1)
        page.fill("#start", "07:00")
        page.dispatch_event("#start", "input")
        time.sleep(2)
        cap("OpenStreetMap places + Open-Meteo weather, then Gemma picks one place and writes the card on my CPU")
        t0 = time.time()
        page.click("#go")
        page.evaluate("window.scrollBy({top: 260, behavior: 'smooth'})")  # keep the live status line in view
        time.sleep(3)
        cap("Gemma 4 E2B is choosing a place and writing the card on this laptop's CPU (no GPU).")
        page.wait_for_selector("#card", timeout=180000)
        took = time.time() - t0
        timeline = {"wait_start": round(t0 - v0, 2), "wait_end": round(time.time() - v0, 2)}
        print(f"plan took {took:.1f}s", file=sys.stderr)
        writer = page.inner_text("#card .badge")
        page.evaluate("document.getElementById('card').scrollIntoView({block: 'start'}); window.scrollBy(0, -60)")
        cap(f"Done in {took:.0f} s. The card says who wrote it: here, Gemma running locally.")
        time.sleep(5)
        cap("Leave, turn-back and home times, walking time and distance come from code and open data, not the model.")
        time.sleep(5)
        page.mouse.wheel(0, 300)
        time.sleep(1)
        cap("Gemma picked the place and wrote the why and the three things to notice. A checker rejects invented places or numbers.")
        time.sleep(6)
        page.mouse.wheel(0, 400)
        time.sleep(1)
        cap("Credits on every card. The turn-back alarm is a calendar file, so the phone's own clock reminds you.")
        time.sleep(5.5)
        page.click("#b-down")
        cap("Phone down: just the place and the turn-back time, plus how long you spent on the screen.")
        time.sleep(8)
        cap("Lock the phone. Go outside.  ·  Code: MIT  ·  Map data © OpenStreetMap contributors  ·  Weather: Open-Meteo")
        time.sleep(4)
        ctx.close()
        browser.close()
        timeline["writer"] = writer
        timeline["plan_seconds"] = round(took, 1)
        timeline["video"] = str(Path(page.video.path()).name)
        (OUT / "timeline.json").write_text(json.dumps(timeline, indent=2))
        print(timeline, file=sys.stderr)


if __name__ == "__main__":
    main()
