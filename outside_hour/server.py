"""A tiny local web server for the one-page app (standard library only)."""

from __future__ import annotations

import json
import re
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import llm
from .card import ics
from .cli import PRESETS
from .planner import NoPlaces, make_plan
from .sources import OfflineMiss, Sources

STATIC = Path(__file__).parent / "static"


class Handler(BaseHTTPRequestHandler):
    offline = False

    def log_message(self, fmt, *args):  # quieter console; no query strings (they contain locations)
        print(f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/health":
            c = llm.LlamaServer()
            name = c.model_name()
            return self._json(200, {"model": name, "model_ok": name != "local model", "offline": self.offline,
                                    "presets": {k: v[0] for k, v in PRESETS.items()}})
        if u.path == "/api/plan":
            return self._plan(q)
        return self._send(404, b"not found", "text/plain")

    def _plan(self, q: dict):
        src = Sources(offline=self.offline or q.get("offline") == "1")
        try:
            minutes = max(20, min(180, int(q.get("minutes", 60))))
            start = q.get("start") or None
            if start and not re.fullmatch(r"\d{1,2}:\d{2}", start):
                return self._json(400, {"error": "start must be HH:MM"})
            if q.get("preset") in PRESETS:
                label, lat, lon = PRESETS[q["preset"]]
            elif q.get("lat") and q.get("lon"):
                label, lat, lon = q.get("label") or "your location", float(q["lat"]), float(q["lon"])
            elif q.get("near"):
                res = src.geocode(q["near"]).data
                if not res:
                    return self._json(404, {"error": f"Couldn't find {q['near']!r} on OpenStreetMap."})
                label, lat, lon = q["near"], float(res[0]["lat"]), float(res[0]["lon"])
            else:
                return self._json(400, {"error": "Tell me where you are."})
            plan = make_plan(src, lat, lon, minutes, label, use_model=q.get("model", "1") == "1", start_at=start)
            d = plan.to_dict()
            d["ics"] = ics(d)
            return self._json(200, d)
        except (NoPlaces, OfflineMiss) as e:
            return self._json(404, {"error": str(e)})
        except Exception as e:  # keep the page alive; details go to the console only
            traceback.print_exc()
            return self._json(500, {"error": f"Something went wrong: {type(e).__name__}"})


def serve(host: str = "127.0.0.1", port: int = 8765, offline: bool = False):
    Handler.offline = offline
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Outside Hour on http://{host}:{port}  (offline={offline})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
