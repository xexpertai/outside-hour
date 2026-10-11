"""Check the model's wording before anyone sees it.

The model may only *choose* among places we fetched and *phrase* facts we gave it.
Anything that looks like an invented place, a wrong place, or a number we never
supplied is rejected, and the planner falls back to a plain template.
"""

from __future__ import annotations

import re

from .places import has_view, has_water

PLACE_WORDS = (
    "Park|Lake|Garden|Gardens|Trail|Road|Pond|Hill|Hills|Temple|Street|Lane|Gate|Cafe|Café|Market|Museum|"
    "Station|Forest|Reserve|Tank|Fort|Beach|River|Bridge|Square|Circle|Nagar|Marg|Lawn|Grove|Woods|Falls|Point"
)
# One to four capitalised words followed by a place word, e.g. "Sunset Point", "Old Mill Road".
PLACE_RE = re.compile(rf"\b((?:[A-Z][\w'’.-]*\s){{1,4}}(?:{PLACE_WORDS}))\b")
NUM_RE = re.compile(r"\d+(?:[.:]\d+)?")

MAX_LEN = {"headline": 80, "why": 230, "phone_down_line": 150, "notice": 120}
# Plain English: ASCII plus common typographic marks and accented Latin letters (place names like "Café").
ALLOWED_CHARS = re.compile(r"^[\x20-\x7E°’‘“”–—…·À-ÿ]*$")


def numbers_in(text: str) -> list[str]:
    out = []
    for n in NUM_RE.findall(text):
        out.append(n)
        if ":" in n:  # 17:40 → also allow "17" and "40" pieces
            out.extend(n.split(":"))
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


TURN_RE = re.compile(r"\bturn\w*(?:\s+\w+){0,3}?\s+(\d{1,2}:\d{2})", re.I)
CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


VOICE_RE = re.compile(r"\b(we|us|me|our|let's|lets)\b", re.I)
# Features the model may only mention when the facts support them (the kind of place, or what OSM maps inside).
FEATURE_RULES = [
    (re.compile(r"\b(ripples?|lake|pond|water'?s?|shore|reflections?)\b", re.I), has_water),
    (re.compile(r"\b(landmarks?|vantage|horizon|skyline)\b", re.I), has_view),
]
# Filler that says nothing about this place. The prompt asks for specifics; this makes it stick.
BLAND_RE = re.compile(r"\b(pleasant (spot|place|walk)|offers? a (pleasant|nice|great|lovely)|"
                      r"enjoy the (park|nature|outdoors|fresh air)|perfect (spot|place)|great (spot|place)|"
                      r"beautiful (spot|place)|connect with nature)\b", re.I)


def check(out: dict, candidates, known_names: list[str], allowed_nums: set[str],
          times: dict | None = None) -> tuple[bool, str]:
    """times: optional {place_id: {"now":..., "turn":..., "back":...}} to check clock times per place."""
    if not isinstance(out, dict):
        return False, "not a JSON object"
    ids = {p.id: p for p in candidates}
    pid = out.get("place_id")
    if pid not in ids:
        return False, f"place_id {pid!r} is not one of the fetched places"
    chosen = ids[pid]
    notice = out.get("notice")
    if not isinstance(notice, list) or len(notice) != 3:
        return False, "notice must be a list of 3 items"
    texts = {"headline": out.get("headline"), "why": out.get("why"), "phone_down_line": out.get("phone_down_line")}
    for i, n in enumerate(notice):
        texts[f"notice[{i}]"] = n
    for k, v in texts.items():
        if not isinstance(v, str) or not v.strip():
            return False, f"{k} is empty"
        if len(v) > MAX_LEN.get(k.split("[")[0], 200):
            return False, f"{k} is too long"
        if not ALLOWED_CHARS.match(v):
            return False, f"{k} contains unexpected characters (possible model glitch)"
        if k in ("why", "phone_down_line") and not v.rstrip().endswith((".", "!", "?")):
            return False, f"{k} looks cut off mid-sentence"

    for k, v in texts.items():
        if VOICE_RE.search(v):
            return False, f"{k} speaks as 'we/us/me' ({VOICE_RE.search(v).group(0)!r})"
        for rx, supported in FEATURE_RULES:
            m = rx.search(v)
            if m and not supported(chosen) and m.group(0).lower() not in chosen.name.lower():
                return False, f"{k} mentions {m.group(0)!r}, which the facts don't support for a {chosen.kind}"
        if k in ("headline", "why") and BLAND_RE.search(v):
            return False, f"{k} is generic filler ({BLAND_RE.search(v).group(0)!r}); say something about this place"

    known = [_norm(n) for n in known_names if n and len(n.strip()) >= 4]
    chosen_n = _norm(chosen.name)
    # Named things mapped inside the chosen place (a pond inside a park) may be mentioned.
    inside = [_norm(n) for n in chosen.features.get("water", []) + chosen.features.get("named_spots", [])]
    known += inside
    for k, v in texts.items():
        low = _norm(v)
        # 1) It must not talk up a different fetched place as the destination.
        for other in candidates:
            on = _norm(other.name)
            if (other.id != pid and len(on) >= 4 and on in low and on not in chosen_n
                    and not any(on in i or i in on for i in inside)):
                return False, f"{k} mentions {other.name!r}, but the chosen place is {chosen.name!r}"
        # 2) Place-like names must exist in the fetched OSM data.
        for m in PLACE_RE.findall(v):
            mn = _norm(m)
            if not any(mn in kn or kn in mn for kn in known):
                return False, f"{k} mentions {m!r}, which is not in the fetched map data"
        # 3) Every number must come from the facts we supplied.
        for n in numbers_in(v):
            if n not in allowed_nums:
                return False, f"{k} contains the number {n}, which is not in the facts"
    if times and pid in times:
        t = times[pid]
        ok_clocks = {t["now"], t["turn"], t["back"]}
        for k, v in texts.items():
            for c in CLOCK_RE.findall(v):
                if c.zfill(5) not in ok_clocks:
                    return False, f"{k} uses the time {c}, which is not this place's plan"
            for c in TURN_RE.findall(v):
                if c.zfill(5) != t["turn"]:
                    return False, f"{k} says turn back at {c}, but the turn-around time is {t['turn']}"
    return True, "ok"
