import pytest

from outside_hour import llm
from outside_hour.card import ics, text_card
from outside_hour.planner import make_plan
from outside_hour.sources import OfflineMiss

from .conftest import LAT, LON, FixtureSources, utc


class FakeModel:
    """Plays the part of the local Gemma server with a scripted answer."""

    label = "fake-gemma (test)"

    def __init__(self, answers):
        self.answers = list(answers)
        self.facts = []

    def write_plan(self, facts):
        self.facts.append(facts)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        out = a(facts) if callable(a) else a
        return out, {"model": "fake", "wall_s": 0.1}


def grounded(facts):
    c = facts["candidates"][1]  # deliberately not the top-ranked one
    return {
        "place_id": c["id"],
        "headline": f"Quiet time at {c['name']}",
        "why": f"About {c['walk_min_one_way']} minutes away and {facts['weather']['sky']} right now.",
        "notice": ["Look for three bark textures.", "Listen for the nearest bird.", "Notice the breeze."],
        "phone_down_line": "Pocket the phone and look up.",
    }


def hallucinated(facts):
    d = grounded(facts)
    d["why"] = "Walk past Sunrise Valley Lake on the way, it is lovely."
    return d


def plan(src, model, hh=7, mm=0, minutes=60, **kw):
    return make_plan(src, LAT, LON, minutes, "MG Road metro (test)", utc_now=utc(hh, mm), llm_client=model, **kw)


def test_grounded_model_output_is_used(src):
    m = FakeModel([grounded])
    p = plan(src, m)
    assert p.writer == "fake-gemma (test)"
    assert p.place["id"] == m.facts[0]["candidates"][1]["id"]  # the model's choice is honoured
    assert p.headline.startswith("Quiet time at")
    assert p.rejected == []
    assert p.phone_down_line.endswith(f"Turn back at {p.turn_around}.")


def test_hallucination_is_rejected_and_template_used(src):
    m = FakeModel([hallucinated, hallucinated])
    p = plan(src, m)
    assert p.writer == "template"
    assert len(p.rejected) == 2 and "Sunrise Valley Lake" in p.rejected[0]
    assert "Sunrise" not in p.why


def test_retry_after_one_bad_draft(src):
    p = plan(src, FakeModel([hallucinated, grounded]))
    assert p.writer == "fake-gemma (test)" and len(p.rejected) == 1


def test_model_down_falls_back_to_template(src):
    p = plan(src, FakeModel([llm.ModelUnavailable("connection refused")]))
    assert p.writer == "template"
    assert "model unavailable" in p.rejected[0]


def test_times_add_up(src):
    p = plan(src, None, use_model=False)
    assert (p.now, p.back_by) == ("07:00", "08:00")
    assert p.walk_min * 2 + p.time_there_min <= 60


def test_cut_short_before_sunset(src):
    p = plan(src, None, hh=17, mm=30, use_model=False)  # sunset 18:03 on 11 Oct
    assert p.minutes < 60
    assert any("before sunset" in w for w in p.warnings)


def test_after_dark_warning_and_torch(src):
    p = plan(src, None, hh=19, mm=30, use_model=False)
    assert any("It is dark" in w for w in p.warnings)
    assert any("torch" in b for b in p.bring)


def test_plan_for_tomorrow_morning(src):
    p = plan(src, None, hh=21, mm=0, use_model=False, start_at="07:00")
    assert p.date == "2026-10-12" and p.now == "07:00"
    assert not any("dark" in w for w in p.warnings)


def test_offline_miss_is_reported():
    s = FixtureSources()
    del s.d["overpass"]
    with pytest.raises(OfflineMiss):
        plan(s, None, use_model=False)


def test_card_and_calendar(src):
    d = plan(src, None, use_model=False).to_dict()
    card = text_card(d)
    assert "OpenStreetMap contributors" in card and d["turn_around"] in card
    cal = ics(d)
    assert "BEGIN:VALARM" in cal and "DTSTART:20261011T070000" in cal


def test_plan_has_direction_and_straight_line_distance(src):
    p = plan(src, None, use_model=False)
    assert 0 <= p.place["bearing_deg"] < 360
    assert p.place["direction"] in p.route
    assert p.place["distance_m"] > 0 and "straight line" in p.route


def test_missing_feature_data_is_noted_not_fatal(src):
    p = plan(src, None, use_model=False)  # the recorded fixture has no feature query
    assert any("Mapped details" in n for n in p.data_notes)


def cubbon_bench_pond():
    s = FixtureSources()
    park = next(e for e in s.d["overpass"]["elements"] if e.get("tags", {}).get("name") == "Cubbon Park")
    b = park["bounds"]
    lat, lon = (b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2
    s.d["overpass_features"] = {"elements": [
        {"type": "node", "id": 1, "lat": lat, "lon": lon, "tags": {"amenity": "bench"}},
        {"type": "node", "id": 2, "lat": lat, "lon": lon, "tags": {"amenity": "bench"}},
        {"type": "way", "id": 3, "tags": {"natural": "water"},
         "bounds": {"minlat": lat, "minlon": lon, "maxlat": lat + 0.0002, "maxlon": lon + 0.0002}},
    ]}
    return s


def test_mapped_features_reach_the_model_and_the_card():
    s = cubbon_bench_pond()
    m = FakeModel([lambda f: dict(grounded(f), place_id=f["candidates"][0]["id"],
                                  headline="Cubbon Park benches", why="Shady benches and a small pond.")])
    p = plan(s, m)
    top = m.facts[0]["candidates"][0]
    assert top["name"] == "Cubbon Park"
    assert "benches" in top["mapped_here"] and not any(c.isdigit() for c in " ".join(top["mapped_here"]))
    assert any("water" in i for i in top["notice_ideas"])  # unlocked by the mapped pond
    assert m.facts[0]["light"] == "early morning"
    assert p.writer == "fake-gemma (test)"  # "pond" passes because OSM maps water inside the park
    assert "2 benches mapped" in p.place["mapped_here"]


def test_template_uses_mapped_details():
    p = plan(cubbon_bench_pond(), None, use_model=False)
    assert p.place["name"] == "Cubbon Park"
    assert "Mapped there: Lotus Pond, Bamboo Island Pond" in p.why
