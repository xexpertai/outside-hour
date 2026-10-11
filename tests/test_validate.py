from outside_hour.places import Place
from outside_hour.validate import check, numbers_in

C = [Place("w1", "Cubbon Park", "park", 0, 0), Place("w2", "Lotus Pond", "water", 0, 0),
     Place("w3", "Queen's Park", "park", 0, 0)]
KNOWN = ["Cubbon Park", "Lotus Pond", "Rose Garden", "MG Road metro station", "Queen's Park"]
NUMS = {"07:40", "07", "40", "15", "25", "23", "1", "2", "3"}


def good(**kw):
    d = {
        "place_id": "w1",
        "headline": "Morning trees at Cubbon Park",
        "why": "It is 15 minutes away and clear at 23°C, so you get 25 minutes under the trees.",
        "notice": ["Find three kinds of bark.", "Listen for parakeets.", "Feel the shade change."],
        "phone_down_line": "Phone in pocket. Turn back at 07:40.",
    }
    d.update(kw)
    return d


def test_accepts_grounded_output():
    assert check(good(), C, KNOWN, NUMS) == (True, "ok")


def test_rejects_unknown_place_id():
    ok, why = check(good(place_id="w9"), C, KNOWN, NUMS)
    assert not ok and "not one of the fetched" in why


def test_rejects_invented_place_name():
    ok, why = check(good(why="Start at Sunrise Hill Point for the view."), C, KNOWN, NUMS)
    assert not ok and "Sunrise Hill Point" in why


def test_rejects_other_candidate_named_as_destination():
    ok, why = check(good(headline="Or head to Queen's Park"), C, KNOWN, NUMS)
    assert not ok and "Queen's Park" in why


def test_allows_known_non_candidate_feature_inside_chosen_place():
    assert check(good(notice=["Smell the Rose Garden.", "Count birds.", "Touch bark."]), C, KNOWN, NUMS)[0]


def test_rejects_made_up_numbers():
    ok, why = check(good(why="It is a 2 km loop that takes 35 minutes."), C, KNOWN, NUMS)
    assert not ok and "35" in why


def test_rejects_wrong_shape():
    assert not check(good(notice=["only one"]), C, KNOWN, NUMS)[0]
    assert not check(good(headline=""), C, KNOWN, NUMS)[0]
    assert not check({"_raw": "not json"}, C, KNOWN, NUMS)[0]


def test_numbers_in_splits_clock_times():
    assert numbers_in("back at 17:40, 2.5 km") == ["17:40", "17", "40", "2.5"]


TIMES = {"w1": {"now": "07:00", "turn": "07:40", "back": "08:00"}}


def test_rejects_wrong_turn_back_time():
    bad = good(phone_down_line="Phone away. Turn around by 08:00 sharp.")
    ok, why = check(bad, C, KNOWN, NUMS | {"08:00", "08", "00"}, TIMES)
    assert not ok and "turn" in why.lower()


def test_clock_times_must_belong_to_the_chosen_place():
    ok, why = check(good(why="Be there by 07:52."), C, KNOWN, NUMS | {"07:52", "52"}, TIMES)
    assert not ok and "07:52" in why
    assert check(good(), C, KNOWN, NUMS, TIMES)[0]


def test_rejects_glitches_and_cut_off_text():
    ok, why = check(good(phone_down_line="Put the phone away and focus on the 산."), C, KNOWN, NUMS)
    assert not ok and "characters" in why
    ok, why = check(good(why="The trees are lovely and you can"), C, KNOWN, NUMS)
    assert not ok and "cut off" in why


def test_rejects_unsupported_features_and_odd_voice():
    ok, why = check(good(notice=["Watch the ripples.", "Count birds.", "Touch bark."]), C, KNOWN, NUMS)
    assert not ok and "ripples" in why
    ok, why = check(good(phone_down_line="Enjoy this moment with us."), C, KNOWN, NUMS)
    assert not ok and "us" in why
    lake = [Place("w2", "Lotus Pond", "water", 0, 0), C[0]]
    assert check(good(place_id="w2", headline="Lotus Pond at dawn", why="Clear and still.",
                      notice=["Watch the ripples.", "Count birds.", "Feel the cool air."]), lake, KNOWN, NUMS)[0]


def park_with_pond():
    return [Place("w1", "Cubbon Park", "park", 0, 0, features={"water": ["Lotus Pond"], "benches": 9}),
            Place("w2", "Lotus Pond", "water", 0, 0), C[2]]


def test_water_allowed_when_osm_maps_water_inside_the_park():
    out = good(headline="Cubbon Park and Lotus Pond",
               notice=["Watch the water surface at Lotus Pond.", "Count birds.", "Touch bark."])
    # Without mapped water the same text is refused...
    assert not check(out, C, KNOWN, NUMS)[0]
    # ...with Lotus Pond mapped inside the chosen park it is fine, even though Lotus Pond is also a candidate.
    assert check(out, park_with_pond(), KNOWN, NUMS) == (True, "ok")


def test_other_candidates_still_rejected_when_park_has_features():
    ok, why = check(good(why="Then walk on to Queen's Park."), park_with_pond(), KNOWN, NUMS)
    assert not ok and "Queen's Park" in why


def test_invented_place_still_rejected_with_features():
    ok, why = check(good(why="Start at the Heron Lake Point."), park_with_pond(), KNOWN, NUMS)
    assert not ok and "not in the fetched map data" in why


def test_rejects_generic_filler():
    ok, why = check(good(why="The park offers a pleasant spot to relax."), C, KNOWN, NUMS)
    assert not ok and "generic" in why
    ok, why = check(good(headline="Enjoy the park this morning"), C, KNOWN, NUMS)
    assert not ok and "generic" in why
