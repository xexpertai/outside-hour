"""Direction/distance maths, OSM 'mapped here' features, and the light-of-day label."""
from datetime import datetime

import pytest

from outside_hour.places import (bearing_deg, compass_word, feature_phrases, has_view, has_water,
                                 haversine_m, parse_elements)
from outside_hour.planner import ideas_for, light_of_day

from .conftest import LAT, LON


def way(id, tags, minlat, minlon, maxlat, maxlon):
    return {"type": "way", "id": id, "tags": tags,
            "bounds": {"minlat": minlat, "minlon": minlon, "maxlat": maxlat, "maxlon": maxlon}}


def node(id, tags, lat, lon):
    return {"type": "node", "id": id, "tags": tags, "lat": lat, "lon": lon}


@pytest.mark.parametrize("dlat,dlon,deg,word", [
    (1, 0, 0, "north"), (0, 1, 90, "east"), (-1, 0, 180, "south"), (0, -1, 270, "west"),
])
def test_bearing_cardinal_points(dlat, dlon, deg, word):
    b = bearing_deg(0, 0, dlat, dlon)
    assert abs(b - deg) < 0.01
    assert compass_word(b) == word


def test_compass_word_boundaries():
    assert compass_word(350) == "north" and compass_word(22) == "north"
    assert compass_word(23) == "north-east" and compass_word(225) == "south-west"


def test_bearing_and_distance_real_pair():
    # MG Road metro station -> Lalbagh main gate (both public demo starts): about 3.4 km, roughly south-west.
    d = haversine_m(12.9755, 77.6068, 12.9507, 77.5848)
    assert 3500 < d < 3700
    assert 200 < bearing_deg(12.9755, 77.6068, 12.9507, 77.5848) < 230


def test_arrow_points_at_nearest_edge_of_a_big_park():
    park = way(1, {"leisure": "park", "name": "Edge Park"}, LAT - 0.01, LON + 0.005, LAT + 0.01, LON + 0.03)
    (p,) = parse_elements([park], LAT, LON)
    assert 85 <= p.bearing_deg <= 95 and p.bearing == "east"   # straight east to the near edge
    assert 500 < p.distance_m < 600


PARK = way(10, {"leisure": "park", "name": "Test Park"}, LAT, LON + 0.004, LAT + 0.004, LON + 0.008)
INSIDE = (LAT + 0.002, LON + 0.006)
OUTSIDE = (LAT + 0.02, LON + 0.02)


def test_features_inside_counts_only_what_is_mapped_inside():
    extra = [
        node(1, {"amenity": "bench"}, *INSIDE), node(2, {"amenity": "bench"}, *INSIDE),
        node(3, {"amenity": "bench"}, *OUTSIDE),
        *[node(10 + i, {"natural": "tree"}, *INSIDE) for i in range(6)],
        node(20, {"amenity": "drinking_water"}, *INSIDE),
        way(21, {"natural": "water"}, INSIDE[0] - 0.0005, INSIDE[1] - 0.0005, INSIDE[0] + 0.0005, INSIDE[1] + 0.0005),
        way(22, {"natural": "water", "water": "drain", "name": "Big Drain"}, *INSIDE, *INSIDE),
    ]
    named = way(30, {"natural": "water", "name": "Kingfisher Pond"}, INSIDE[0], INSIDE[1], INSIDE[0] + 0.0003, INSIDE[1] + 0.0003)
    places = {p.name: p for p in parse_elements([PARK, named], LAT, LON, extra=extra)}
    f = places["Test Park"].features
    assert f["benches"] == 2 and f["trees"] == 6 and f["drinking_water"] is True
    assert f["water"] == ["Kingfisher Pond"]
    assert "Big Drain" not in str(f)
    assert has_water(places["Test Park"]) and not has_view(places["Test Park"])
    card = feature_phrases(places["Test Park"])
    assert "Kingfisher Pond (water)" in card and "2 benches mapped" in card and "6 trees mapped" in card
    model = feature_phrases(places["Test Park"], counts=False)
    assert "benches" in model and not any(ch.isdigit() for ch in " ".join(model))


def test_park_without_water_has_no_water():
    (p,) = parse_elements([PARK], LAT, LON, extra=[node(1, {"amenity": "bench"}, *INSIDE)])
    assert not has_water(p) and feature_phrases(p) == ["a bench mapped"]


def test_light_of_day_labels():
    sr, ss = datetime(2026, 10, 12, 6, 8), datetime(2026, 10, 12, 18, 3)
    at = lambda h, m=0: light_of_day(datetime(2026, 10, 12, h, m), sr, ss)  # noqa: E731
    assert at(5, 30) == "after dark" and at(19) == "after dark"
    assert at(7) == "early morning"
    assert at(9) == "morning" and at(12) == "midday" and at(15) == "afternoon"
    assert at(17) == "before sunset"


def test_ideas_follow_features_and_light():
    (p,) = parse_elements([PARK], LAT, LON, extra=[node(1, {"amenity": "bench"}, *INSIDE)])
    ideas = ideas_for(p, "early morning", wet=False)
    assert any("bench" in i for i in ideas) and any("dew" in i for i in ideas)
    assert not any("water" in i for i in ideas)
    assert len(ideas) <= 5
