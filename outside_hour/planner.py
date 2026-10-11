"""Build one outdoor plan: deterministic facts first, then local-model wording, then validation."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from . import llm, validate
from .places import Place, feature_phrases, has_water, parse_elements, rank, search_radius_m
from .sources import Sources
from .weather import Window, local_now, summarise

NOTICE_BY_KIND = {
    "park": ["Find three trees with different bark.", "Listen for the loudest bird and look for it.",
             "Notice where people sit, and where nobody does."],
    "garden": ["Find one flower that insects keep visiting.", "Smell three different leaves.",
               "Look for something that was planted on purpose and something that wasn't."],
    "water": ["Watch the water surface for one full minute.", "Count the kinds of birds near the edge.",
              "Notice how the air feels different near the water."],
    "viewpoint": ["Name the furthest thing you can see.", "Watch how the light changes on one spot.",
                  "Look for one landmark you have never noticed before."],
    "woodland": ["Find the oldest-looking tree.", "Stand still and count the sounds you hear.",
                 "Look down: find moss, fungi or fallen seeds."],
}
# Hand-written ideas the model may pick from and adapt (it still has to fit weather and time of day).
NOTICE_IDEAS = {
    "park": ["three trees with different bark", "the loudest bird, then find it", "where people sit and where nobody does",
             "a patch of shade that is noticeably cooler", "fallen leaves, flowers or seeds on the path",
             "how many shades of green you can count"],
    "garden": ["one flower that insects keep visiting", "the smell of three different leaves",
               "something planted on purpose and something that wasn't", "a plant with a label you can read",
               "how the light falls through the leaves", "dew or dust on the leaves"],
    "water": ["the water surface for one full minute", "how many kinds of birds are near the edge",
              "how the air feels near the water", "reflections that move and ones that don't",
              "where the water is still and where it moves"],
    "viewpoint": ["the furthest thing you can see", "how the light changes on one spot", "a landmark you never noticed"],
    "hilltop": ["the furthest thing you can see", "how the wind changes as you climb", "the town noise fading"],
    "woodland": ["the oldest-looking tree", "how many sounds you can count while standing still",
                 "moss, fungi or seeds on the ground", "the smell of the soil"],
    "nature reserve": ["the oldest-looking tree", "an animal track or a feather", "how many birdsongs you can tell apart"],
}

# Ideas unlocked by what OpenStreetMap maps inside a place (see places.features_inside).
FEATURE_IDEAS = {
    "water": "the water surface for one full minute",
    "trees": "the biggest tree you can find, and the shape of its bark",
    "benches": "one bench, sitting on it for two minutes with your eyes closed",
    "woods_or_tree_rows": "the shade under the thickest group of trees",
    "viewpoint": "the furthest thing you can see from the viewpoint",
    "named_spots": "the sign or entrance of the spot inside it",
    "playground": "the sounds from the playground, then the quiet beyond it",
    "drinking_water": "the drinking-water tap, to refill before you turn back",
}
# Ideas that depend on the light, so the same park reads differently at 07:00 and at 17:30.
LIGHT_IDEAS = {
    "early morning": "dew or wet leaves, and how long the shadows are",
    "morning": "which side of the paths is in sun and which in shade",
    "midday": "the coolest patch of shade",
    "afternoon": "how warm a sunny bench or stone feels compared with a shady one",
    "before sunset": "the colour of the sky changing over a few minutes",
    "after dark": "which lights come on, and what sounds are left",
}

DEFAULT_NOTICE = ["Look up at the sky for a full minute.", "Find one thing that is a colour you rarely see.",
                  "Notice the smell of the air when you arrive."]

ATTRIBUTION = "Map data © OpenStreetMap contributors (ODbL). Weather & air quality: Open-Meteo.com (CC BY 4.0)."


@dataclass
class Plan:
    start_label: str
    lat: float
    lon: float
    now: str
    date: str
    minutes_asked: int
    minutes: int
    place: dict
    alternatives: list
    walk_min: int
    time_there_min: int
    turn_around: str
    back_by: str
    route: str
    weather: dict
    bring: list
    warnings: list
    headline: str
    why: str
    notice: list
    phone_down_line: str
    writer: str
    rejected: list = field(default_factory=list)
    model_stats: dict = field(default_factory=dict)
    data_notes: list = field(default_factory=list)
    light: str = ""
    attribution: str = ATTRIBUTION
    timings_s: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def hhmm(t: datetime) -> str:
    return t.strftime("%H:%M")


def bring_list(w: Window, minutes: int, after_dark: bool) -> list[str]:
    items = ["Keys and this card (phone can stay in your pocket)"]
    if w.rain_pct >= 40 or w.wet:
        items.append(f"Umbrella or rain jacket (rain chance {w.rain_pct}%)")
    if w.feels_c >= 28 or minutes >= 60:
        items.append("Water bottle")
    if w.uv >= 6:
        items.append(f"Cap and sunscreen (UV {w.uv:.0f})")
    if w.feels_c <= 12:
        items.append(f"Warm layer (feels like {w.feels_c:.0f}°C)")
    if w.wind_kmh >= 30:
        items.append(f"Windproof layer (wind {w.wind_kmh:.0f} km/h)")
    if after_dark:
        items.append("A small torch or reflective layer")
    return items


def warnings_for(w: Window, after_dark: bool, cut: bool) -> list[str]:
    out = []
    if w.thunder:
        out.append("Thunderstorm in the forecast. Consider going later; if you go, stay close to shelter.")
    if w.aqi is not None and w.aqi > 150:
        out.append(f"Air quality is poor (US AQI {w.aqi}). Keep it gentle and short today.")
    elif w.aqi is not None and w.aqi > 100:
        out.append(f"Air quality is moderate-to-poor (US AQI {w.aqi}). Sensitive people should take it easy.")
    if after_dark:
        ss = hhmm(w.sunset) if w.sunset else "earlier"
        out.append(f"It is dark (sunset {ss}). Stick to lit, busy paths; this plan keeps you close.")
    if cut:
        out.append(f"Shortened so you are back before sunset ({hhmm(w.sunset)}).")
    return out


def light_of_day(now: datetime, sunrise: datetime | None, sunset: datetime | None) -> str:
    """A plain label for the light while you are out (from code, so the model can't get it wrong)."""
    if sunrise and sunset:
        if now < sunrise or now >= sunset:
            return "after dark"
        if now < sunrise + timedelta(hours=2):
            return "early morning"
        if sunset - now <= timedelta(minutes=90):
            return "before sunset"
    h = now.hour
    if h < 11:
        return "morning"
    if h < 15:
        return "midday"
    return "afternoon"


def ideas_for(p: Place, light: str, wet: bool) -> list[str]:
    """Up to five 'things to notice' ideas for one candidate: its features first, then the light, then its kind."""
    out: list[str] = []
    f = p.features
    if has_water(p):
        out.append(FEATURE_IDEAS["water"])
    for key in ("trees", "woods_or_tree_rows", "benches", "viewpoint", "named_spots", "playground"):
        if f.get(key) and FEATURE_IDEAS[key] not in out:
            out.append(FEATURE_IDEAS[key])
    if light in LIGHT_IDEAS:
        out.append(LIGHT_IDEAS[light])
    if wet:
        out.append("the smell of wet soil and leaves")
    for idea in NOTICE_IDEAS.get(p.kind, []):
        if idea not in out:
            out.append(idea)
    return out[:5]


def allowed_numbers(plan_facts: dict) -> set[str]:
    nums: set[str] = set()

    def add(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            nums.add(str(int(v)) if float(v).is_integer() else str(v))
            nums.add(str(int(round(v))))
        elif isinstance(v, str):
            for n in validate.numbers_in(v):
                nums.add(n)
        elif isinstance(v, dict):
            for x in v.values():
                add(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                add(x)

    add(plan_facts)
    # "3 things to notice" style counting words are harmless
    nums.update({"1", "2", "3", "one", "two", "three"})
    return nums


def make_plan(src: Sources, lat: float, lon: float, minutes: int, start_label: str,
              use_model: bool = True, utc_now: datetime | None = None, llm_client=None,
              start_at: str | None = None) -> Plan:
    """start_at: optional local "HH:MM" to plan for later (the next time that clock time comes round)."""
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    notes: list[str] = []

    fc = src.forecast(lat, lon)
    try:
        aq = src.air(lat, lon)
    except Exception:
        aq = None
        notes.append("Air quality unavailable.")
    now = local_now(fc.data, utc_now)
    if start_at:
        hh, mm = (int(x) for x in start_at.split(":"))
        later = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        now = later if later >= now else later + timedelta(days=1)
    for name, f in (("weather", fc), ("air quality", aq)):
        if f and f.from_cache:
            age = int((time.time() - f.fetched_at) / 60) if utc_now is None else None
            if age is not None and age > 90:
                notes.append(f"{name.capitalize()} from cache, {age // 60} h old.")

    w0 = summarise(fc.data, aq.data if aq else None, now, minutes)
    sunset, sunrise = w0.sunset, w0.sunrise
    after_dark = bool(sunset and sunrise and (now >= sunset or now < sunrise))
    eff = minutes
    cut = False
    if sunset and not after_dark:
        to_sunset = int((sunset - now).total_seconds() // 60) - 10
        if to_sunset < minutes:
            eff, cut = max(15, to_sunset), True
    w = summarise(fc.data, aq.data if aq else None, now, eff)
    timings["weather"] = round(time.perf_counter() - t0, 2)

    t1 = time.perf_counter()
    radius = search_radius_m(max(eff, 90))  # one cache entry per spot for any trip up to 90 min
    op = src.overpass(lat, lon, radius)
    extra: list = []
    try:  # benches, trees, taps, unnamed ponds: makes the card specific, but is optional
        extra = src.overpass_features(lat, lon, radius).data.get("elements", [])
    except Exception:
        notes.append("Mapped details (benches, trees, ponds) unavailable right now.")
    places = parse_elements(op.data.get("elements", []), lat, lon, extra=extra)
    budget = eff // 2 if after_dark else eff
    cands = rank(places, budget, wet=w.wet, limit=5)
    timings["places"] = round(time.perf_counter() - t1, 2)
    if not cands:
        raise NoPlaces(f"No named parks, water, viewpoints or paths found within {radius} m in OpenStreetMap.")

    light = light_of_day(now, sunrise, sunset)
    weather = {
        "sky": w.sky, "temp_c": w.temp_c, "feels_like_c": w.feels_c, "rain_chance_pct": w.rain_pct,
        "wind_kmh": w.wind_kmh, "uv_index": w.uv, "us_aqi": w.aqi, "pm2_5": w.pm25,
        "sunrise": hhmm(sunrise) if sunrise else None, "sunset": hhmm(sunset) if sunset else None,
        "timezone": w.tz_name,
    }
    bring = bring_list(w, eff, after_dark)
    warns = warnings_for(w, after_dark, cut)

    def timing_for(p: Place) -> dict:
        walk = p.walk_min
        there = max(5, eff - 2 * walk - 5)  # 5 minutes of slack
        turn = now + timedelta(minutes=walk + there)
        back = now + timedelta(minutes=eff)
        return {"walk": walk, "there": there, "turn": hhmm(turn), "back": hhmm(back)}

    facts = {
        "minutes_available": eff,
        "local_time_now": hhmm(now),
        "light": light,
        "weather": {k: v for k, v in weather.items() if k not in ("timezone", "pm2_5", "sunrise") and v is not None},
        "warnings": warns,
        "candidates": [dict({k: v for k, v in p.as_fact().items() if v not in (None, {}, "", [])},
                            **{"time_there_min": timing_for(p)["there"], "turn_around_at": timing_for(p)["turn"],
                               "back_by": timing_for(p)["back"], "notice_ideas": ideas_for(p, light, w.wet)})
                       for p in cands],
    }
    known_names = [p.name for p in places] + [start_label]
    times = {p.id: dict(timing_for(p), now=hhmm(now)) for p in cands}

    chosen = cands[0]
    prose = None
    rejected: list[str] = []
    stats: dict = {}
    writer = "template"
    if use_model:
        t2 = time.perf_counter()
        client = llm_client or llm.LlamaServer()
        try:
            for attempt in range(2):
                out, stats = client.write_plan(facts)
                ok, reason = validate.check(out, cands, known_names, allowed_numbers(facts), times)
                if ok:
                    chosen = next(p for p in cands if p.id == out["place_id"])
                    prose = out
                    writer = client.label
                    break
                rejected.append(reason)
        except llm.ModelUnavailable as e:
            rejected.append(f"model unavailable: {e}")
        timings["model"] = round(time.perf_counter() - t2, 2)
    if prose is None:
        prose = template_prose(chosen, timing_for(chosen), w, light)

    tm = timing_for(chosen)
    line = prose["phone_down_line"].strip()
    if tm["turn"] not in line:  # the turn-around time always comes from code, never from the model
        line = f"{line.rstrip('.!')}. Turn back at {tm['turn']}."
    route = (f"Walk about {tm['walk']} min {chosen.bearing} to {chosen.name} "
             f"(~{int(round(chosen.distance_m / 100.0) * 100)} m in a straight line). "
             f"Spend about {tm['there']} min there. Turn back at {tm['turn']}.")
    timings["total"] = round(time.perf_counter() - t0, 2)
    return Plan(
        start_label=start_label, lat=lat, lon=lon, now=hhmm(now), date=now.date().isoformat(), minutes_asked=minutes, minutes=eff,
        place={"id": chosen.id, "name": chosen.name, "kind": chosen.kind, "lat": chosen.lat, "lon": chosen.lon,
               "osm_url": chosen.osm_url, "details": chosen.details, "mapped_here": feature_phrases(chosen),
               "distance_m": int(round(chosen.distance_m)), "bearing_deg": chosen.bearing_deg,
               "direction": chosen.bearing},
        light=light,
        alternatives=[{"name": p.name, "kind": p.kind, "walk_min": p.walk_min, "direction": p.bearing}
                      for p in cands if p.id != chosen.id][:3],
        walk_min=tm["walk"], time_there_min=tm["there"], turn_around=tm["turn"], back_by=tm["back"],
        route=route, weather=weather, bring=bring, warnings=warns,
        headline=prose["headline"], why=prose["why"], notice=list(prose["notice"]),
        phone_down_line=line, writer=writer, rejected=rejected,
        model_stats=stats, data_notes=notes, timings_s=timings,
    )


def template_prose(p: Place, tm: dict, w: Window, light: str = "") -> dict:
    mapped = [m.replace(" (water)", "").replace(" (in or beside it)", "") for m in feature_phrases(p)]
    extra = f" Mapped there: {', '.join(mapped[:3])}." if mapped else ""
    notice = list(NOTICE_BY_KIND.get(p.kind, DEFAULT_NOTICE))
    if has_water(p) and p.kind != "water":
        notice[2] = "Watch the water surface for one full minute."
    if light in LIGHT_IDEAS:
        notice[1] = "Notice " + LIGHT_IDEAS[light] + "."
    return {
        "place_id": p.id,
        "headline": f"{p.name}, there and back by {tm['back']}",
        "why": f"A {p.kind} about {tm['walk']} minutes' walk away, which leaves about {tm['there']} minutes "
               f"there. Forecast: {w.sky}, {w.temp_c:.0f}°C.{extra}",
        "notice": notice,
        "phone_down_line": "Phone in your pocket. Look up.",
    }


class NoPlaces(RuntimeError):
    pass
