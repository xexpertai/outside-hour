from outside_hour.places import compass, haversine_m, parse_elements, rank, search_radius_m

from .conftest import LAT, LON


def el(id, tags, lat=None, lon=None, bounds=None, type="way"):
    e = {"type": type, "id": id, "tags": tags}
    if type == "node":
        e.update(lat=lat, lon=lon)
    else:
        e["bounds"] = bounds
    return e


def box(lat, lon, d=0.002):
    return {"minlat": lat - d, "maxlat": lat + d, "minlon": lon - d, "maxlon": lon + d}


def test_haversine_and_compass():
    assert 1100 < haversine_m(0, 0, 0.01, 0) < 1120
    assert compass(0, 0, 1, 0) == "north"
    assert compass(0, 0, 0, -1) == "west"


def test_unnamed_and_disrespectful_places_are_dropped():
    els = [
        el(1, {"leisure": "park"}, bounds=box(LAT + 0.005, LON)),  # no name
        el(2, {"natural": "wood", "name": "Old Christian Cemetery"}, bounds=box(LAT + 0.005, LON)),
        el(3, {"leisure": "park", "name": "Private Lawn", "access": "private"}, bounds=box(LAT, LON + 0.005)),
        el(4, {"leisure": "park", "name": "Good Park"}, bounds=box(LAT, LON - 0.006)),
        el(5, {"tourism": "viewpoint"}, lat=LAT + 0.003, lon=LON, type="node"),
        el(6, {"natural": "water", "water": "drain", "name": "K100"}, bounds=box(LAT, LON + 0.004)),
    ]
    names = sorted(p.name for p in parse_elements(els, LAT, LON))
    assert names == ["Good Park", "Unnamed viewpoint"]


def test_big_park_distance_is_to_its_edge_not_centre():
    big = el(1, {"leisure": "park", "name": "Huge Park"}, bounds=box(LAT, LON + 0.02, d=0.015))
    (p,) = parse_elements([big], LAT, LON)
    # centre is ~2.2 km away, the near edge ~0.5 km
    assert p.distance_m < 700
    assert p.size_ha > 100


def test_rank_respects_time_budget_and_prefers_green_space(src):
    places = parse_elements(src.d["overpass"]["elements"], LAT, LON)
    top = rank(places, 30)
    assert top, "expected candidates near MG Road"
    assert all(p.walk_min <= 9 for p in top)  # 30 % of 30 minutes
    top60 = rank(places, 60)
    assert top60[0].name == "Cubbon Park"


def test_search_radius_is_bounded_and_rounded():
    assert search_radius_m(20) == 500
    assert search_radius_m(600) == 4000
    assert search_radius_m(60) % 250 == 0
