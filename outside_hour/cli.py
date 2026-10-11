"""Command line: `python -m outside_hour plan --near "Cubbon Park, Bengaluru" --minutes 60`."""

from __future__ import annotations

import argparse
import json
import re
import sys

from .card import text_card
from .planner import NoPlaces, make_plan
from .sources import OfflineMiss, Sources

PRESETS = {
    # Public places only, used for demos and screenshots.
    "mg-road": ("MG Road metro station, Bengaluru (demo start)", 12.9755, 77.6068),
    "lalbagh": ("Lalbagh main gate, Bengaluru (demo start)", 12.9507, 77.5848),
    "pune-empress": ("Empress Garden, Pune (demo start)", 18.5089, 73.8986),
}


def resolve(src: Sources, near: str | None, lat, lon) -> tuple[str, float, float]:
    if lat is not None and lon is not None:
        return (near or f"{lat:.4f}, {lon:.4f}"), lat, lon
    if not near:
        raise SystemExit("Give --near \"place\", --preset, or --lat/--lon.")
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", near)
    if m:
        return near, float(m.group(1)), float(m.group(2))
    res = src.geocode(near).data
    if not res:
        raise SystemExit(f"Could not find {near!r} in OpenStreetMap (Nominatim).")
    return near, float(res[0]["lat"]), float(res[0]["lon"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="outside-hour", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan", help="make one outdoor plan and print a pocket card")
    pp.add_argument("--near", help="a place name or 'lat,lon'")
    pp.add_argument("--preset", choices=sorted(PRESETS))
    pp.add_argument("--lat", type=float)
    pp.add_argument("--lon", type=float)
    pp.add_argument("--minutes", type=int, default=60)
    pp.add_argument("--start", help="plan for later today/tomorrow at local HH:MM (e.g. 07:00)")
    pp.add_argument("--offline", action="store_true", help="use cached data only; never touch the network")
    pp.add_argument("--no-model", action="store_true", help="skip the local model; use the plain template")
    pp.add_argument("--json", action="store_true", help="print the full plan as JSON")
    sp = sub.add_parser("serve", help="run the one-page web app")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .server import serve
        serve(a.host, a.port, offline=a.offline)
        return 0

    src = Sources(offline=a.offline)
    if a.preset:
        label, lat, lon = PRESETS[a.preset]
    else:
        label, lat, lon = resolve(src, a.near, a.lat, a.lon)
    minutes = max(20, min(180, a.minutes))
    try:
        plan = make_plan(src, lat, lon, minutes, label, use_model=not a.no_model, start_at=a.start)
    except (NoPlaces, OfflineMiss, RuntimeError) as e:
        print(f"Sorry: {e}", file=sys.stderr)
        return 2
    d = plan.to_dict()
    if a.json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        print(text_card(d))
        if d["model_stats"]:
            s = d["model_stats"]
            print(f"  Model: {s['model']} · {s['prompt_tokens']} prompt tok @ {s['prompt_tok_per_s']}/s · "
                  f"{s['output_tokens']} output tok @ {s['output_tok_per_s']}/s · {s['wall_s']} s")
        print(f"  Timings (s): {d['timings_s']}")
    return 0
