"""Turn raw OpenStreetMap elements into walkable candidate places."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

WALK_M_PER_MIN = 75.0  # ~4.5 km/h, an easy walking pace
DETOUR = 1.3  # real paths are longer than a straight line

KIND_LABELS = {
    ("leisure", "park"): "park",
    ("leisure", "garden"): "garden",
    ("leisure", "nature_reserve"): "nature reserve",
    ("tourism", "viewpoint"): "viewpoint",
    ("natural", "water"): "water",
    ("natural", "peak"): "hilltop",
    ("natural", "wood"): "woodland",
    ("natural", "beach"): "beach",
    ("natural", "tree_row"): "tree-lined walk",
    ("landuse", "forest"): "woodland",
    ("highway", "path"): "path",
    ("highway", "footway"): "footpath",
    ("highway", "track"): "track",
}

# How much a kind of place is "worth" for getting outside (tie-breaker only).
KIND_BONUS = {
    "nature reserve": 3, "woodland": 3, "viewpoint": 3, "hilltop": 3, "water": 2,
    "beach": 3, "garden": 2, "park": 2, "tree-lined walk": 1, "path": 1, "footpath": 0, "track": 1,
}

DETAIL_TAGS = ("water", "garden:type", "opening_hours", "access", "wheelchair", "lit", "surface",
               "ele", "description", "fee", "dog", "heritage")


@dataclass
class Place:
    id: str
    name: str
    kind: str
    lat: float
    lon: float
    distance_m: float = 0.0
    bearing: str = ""
    walk_min: int = 0
    details: dict = field(default_factory=dict)
    osm_url: str = ""
    size_ha: float = 0.0
    bearing_deg: int = 0  # 0 = north, 90 = east; to the nearest point of the place
    bbox: tuple = ()  # (minlat, minlon, maxlat, maxlon), for "what is mapped inside"
    features: dict = field(default_factory=dict)  # from OSM: water, benches, named spots inside, ...

    def as_fact(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "walk_min_one_way": self.walk_min,
            "direction": self.bearing,
            "approx_size_ha": self.size_ha or None,
            "details": self.details,
            "water_here": self.features.get("water", []),
            "spots_in_or_beside": self.features.get("named_spots", [])[:2],
            "mapped_here": [m for m in feature_phrases(self, counts=False) if not m.endswith(("(water)", "(in or beside it)"))],
        }


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    """Initial great-circle bearing from point 1 to point 2, in degrees clockwise from north."""
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass_word(deg: float) -> str:
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][
        int((deg + 22.5) // 45) % 8]


def compass(lat1, lon1, lat2, lon2) -> str:
    return compass_word(bearing_deg(lat1, lon1, lat2, lon2))


def kind_of(tags: dict) -> str | None:
    for (k, v), label in KIND_LABELS.items():
        if tags.get(k) == v:
            return label
    return None


EXCLUDE_NAME = re.compile(r"cemetery|cemetary|graveyard|grave yard|burial|crematorium|kabrastan|qabrastan|"
                          r"smashan|shmashan|military|cantonment|airport|prison|jail", re.I)
EXCLUDE_TAGS = {("landuse", "cemetery"), ("amenity", "grave_yard"), ("landuse", "military"),
                ("military", "barracks"), ("access", "private"), ("access", "no")}


def parse_elements(elements: list, here_lat: float, here_lon: float, extra: list = ()) -> list[Place]:
    """Parse Overpass JSON elements. Unnamed places are dropped (you can't be sent to them).

    extra: elements that are only used as features inside places (benches, trees, unnamed ponds)."""
    best: dict[str, Place] = {}
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name:en") or tags.get("name") or "").strip()
        kind = kind_of(tags)
        if not kind:
            continue
        if (EXCLUDE_NAME.search(name) or any(tags.get(k) == v for k, v in EXCLUDE_TAGS)
                or tags.get("water") in ("drain", "ditch", "wastewater", "sewage") or tags.get("covered") == "yes"
                or tags.get("type") == "waterway" or "waterway" in tags):
            continue  # respectful / not a place to send someone for a walk
        if not name:
            if kind != "viewpoint":
                continue
            name = "Unnamed viewpoint"
        size_ha = 0.0
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
            if lat is None or lon is None:
                continue
            d = haversine_m(here_lat, here_lon, lat, lon)
            tlat, tlon = lat, lon
            r = 0.0007  # ~75 m around a point (a viewpoint, a peak): "at" the place
            bbox = (lat - r, lon - r, lat + r, lon + r)
        else:
            b = el.get("bounds") or {}
            if not b:
                continue
            lat = (b["minlat"] + b["maxlat"]) / 2
            lon = (b["minlon"] + b["maxlon"]) / 2
            h = haversine_m(b["minlat"], lon, b["maxlat"], lon)
            w = haversine_m(lat, b["minlon"], lat, b["maxlon"])
            if kind not in ("path", "footpath", "track", "tree-lined walk"):
                size_ha = round(h * w / 10000 * 0.6, 1)  # a bounding box overstates area; 0.6 is a rough fill
            # Walk to the nearest edge of the bounding box, not its centre (big parks).
            clat = min(max(here_lat, b["minlat"]), b["maxlat"])
            clon = min(max(here_lon, b["minlon"]), b["maxlon"])
            d = max(100.0, haversine_m(here_lat, here_lon, clat, clon))
            bbox = (b["minlat"], b["minlon"], b["maxlat"], b["maxlon"])
            # Point the arrow at the nearest edge; if you are already inside, at the middle.
            tlat, tlon = (clat, clon) if (clat, clon) != (here_lat, here_lon) else (lat, lon)
        deg = bearing_deg(here_lat, here_lon, tlat, tlon)
        p = Place(
            id=f"{el['type'][0]}{el['id']}",
            name=name,
            kind=kind,
            lat=lat,
            lon=lon,
            distance_m=d,
            bearing=compass_word(deg),
            bearing_deg=int(round(deg)) % 360,
            bbox=bbox,
            walk_min=max(1, round(d * DETOUR / WALK_M_PER_MIN)),
            details={k: tags[k] for k in DETAIL_TAGS if k in tags},
            osm_url=f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            size_ha=size_ha,
        )
        # The same name often appears as several OSM objects; keep the closest.
        key = name.lower()
        if key not in best or p.distance_m < best[key].distance_m:
            best[key] = p
    out = list(best.values())
    everything = list(elements) + list(extra)
    for p in out:
        p.features = features_inside(p, everything)
    return out


def _point(el: dict) -> tuple[float, float] | None:
    if el.get("type") == "node":
        return (el["lat"], el["lon"]) if el.get("lat") is not None else None
    b = el.get("bounds")
    return ((b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2) if b else None


def features_inside(p: Place, elements: list) -> dict:
    """What OpenStreetMap maps inside the place's outline box (or within ~75 m of a point place).

    A bounding box is generous for odd-shaped parks, so the card says "mapped in or next to it".
    Only things that are actually in the fetched data are listed; nothing is guessed.
    """
    if not p.bbox:
        return {}
    a, b, c, d = p.bbox
    f: dict = {}
    named: list[str] = []
    for el in elements:
        tags = el.get("tags") or {}
        pt = _point(el)
        if not pt or not (a <= pt[0] <= c and b <= pt[1] <= d):
            continue
        if f"{el.get('type', '')[:1]}{el.get('id')}" == p.id:
            continue
        nm = (tags.get("name:en") or tags.get("name") or "").strip()
        if (tags.get("water") in ("drain", "ditch", "wastewater", "sewage") or "waterway" in tags
                or tags.get("covered") == "yes" or EXCLUDE_NAME.search(nm)):
            continue
        am, nat, lei = tags.get("amenity"), tags.get("natural"), tags.get("leisure")
        if am == "bench":
            f["benches"] = f.get("benches", 0) + 1
        elif am == "drinking_water":
            f["drinking_water"] = True
        elif am == "toilets":
            f["toilets"] = True
        elif lei == "playground":
            f["playground"] = True
        elif nat == "tree":
            f["trees"] = f.get("trees", 0) + 1
        elif nat == "water":
            f.setdefault("water", [])
            if nm and nm.lower() != p.name.lower() and nm not in f["water"]:
                f["water"].append(nm)
            elif not nm:
                f["unnamed_water"] = f.get("unnamed_water", 0) + 1
        elif nat in ("wood", "tree_row") or tags.get("landuse") == "forest":
            f["woods_or_tree_rows"] = True
        elif tags.get("tourism") == "viewpoint":
            f["viewpoint"] = True
        elif nm and kind_of(tags) in ("park", "garden", "nature reserve", "hilltop") and nm.lower() != p.name.lower():
            if nm not in named:
                named.append(nm)
        elif nm and kind_of(tags) in ("footpath", "path", "track", "tree-lined walk") and tags.get("lit") == "yes":
            f["lit_paths"] = True
    if named:
        f["named_spots"] = sorted(named, key=lambda n: (len(n), n))[:3]
    if f.get("water") == [] and not f.get("unnamed_water"):
        f.pop("water")
    if "water" in f:
        f["water"] = f["water"][:3]
    return f


def has_water(p: Place) -> bool:
    return p.kind in ("water", "beach") or bool(p.features.get("water") or p.features.get("unnamed_water"))


def has_view(p: Place) -> bool:
    return p.kind in ("viewpoint", "hilltop") or bool(p.features.get("viewpoint"))


def feature_phrases(p: Place, counts: bool = True) -> list[str]:
    """Short, factual phrases for the card (with counts) and the model (counts=False, so it doesn't quote
    survey-like numbers). Counts are what OSM volunteers have mapped, not a census."""
    f, out = p.features, []

    def n(key, one, many, lots_at=20):
        c = f.get(key, 0)
        if counts:
            return f"{c} {many} mapped" if c > 1 else f"{one} mapped"
        return f"lots of {many}" if c >= lots_at else (many if c > 1 else one)

    for w in f.get("water", []):
        out.append(f"{w} (water)")
    if f.get("unnamed_water") and not f.get("water") and p.kind != "water":
        out.append("a small pond (unnamed on the map)")
    if f.get("trees", 0) >= 5:
        out.append(n("trees", "a tree", "trees"))
    if f.get("woods_or_tree_rows"):
        out.append("a wooded patch or tree row")
    if f.get("benches"):
        out.append(n("benches", "a bench", "benches"))
    if f.get("viewpoint"):
        out.append("a viewpoint")
    for s in f.get("named_spots", [])[:2]:
        out.append(f"{s} (in or beside it)")
    if f.get("lit_paths"):
        out.append("lit footpaths")
    if f.get("drinking_water"):
        out.append("a drinking-water tap")
    if f.get("toilets"):
        out.append("public toilets")
    if f.get("playground"):
        out.append("a playground")
    d = p.details
    if d.get("garden:type"):
        out.append(f"a {d['garden:type'].replace('_', ' ')} garden")
    if d.get("water") and p.kind == "water":
        out.append(f"a {d['water'].replace('_', ' ')}")
    if d.get("heritage"):
        out.append("a listed heritage site")
    if d.get("lit") == "yes":
        out.append("lit at night")
    if d.get("surface"):
        out.append(f"{d['surface'].replace('_', ' ')} surface")
    return out[:8]


def search_radius_m(minutes: int) -> int:
    """Furthest a place can be and still leave real time there (out-and-back walk ≤ 60%)."""
    one_way_min = minutes * 0.3
    r = min(4000, max(500, one_way_min * WALK_M_PER_MIN / DETOUR))
    return int(-(-r // 250) * 250)  # round up to 250 m so nearby requests share a cache entry


def rank(places: list[Place], minutes: int, wet: bool = False, limit: int = 6) -> list[Place]:
    """Keep places that fit the time budget and rank them; deterministic."""
    max_walk = minutes * 0.3
    ideal = minutes * 0.2
    fits = [p for p in places if p.walk_min <= max_walk]

    def score(p: Place) -> float:
        s = KIND_BONUS.get(p.kind, 0) * 2.0
        if p.size_ha:  # bigger green spaces are better for an hour outside: +0 (1 ha) .. +6 (1000 ha)
            s += min(3.0, max(0.0, math.log10(p.size_ha))) * 2.0
        # Walking further than needed eats into time outside; being close is fine.
        if p.walk_min > ideal:
            s -= (p.walk_min - ideal) / max(ideal, 1) * 3.0
        if wet and p.kind in ("hilltop", "track", "path", "beach"):
            s -= 3  # muddy or exposed in rain
        if p.details.get("access") in ("private", "no"):
            s -= 100
        if p.name == "Unnamed viewpoint":
            s -= 2
        return s

    fits.sort(key=lambda p: (-score(p), p.distance_m, p.name))
    fits = [p for p in fits if p.details.get("access") not in ("private", "no")]
    return fits[:limit]
