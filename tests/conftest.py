import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from outside_hour.sources import Fetched, OfflineMiss

FIX = Path(__file__).parent / "fixtures"
LAT, LON = 12.9755, 77.6068  # MG Road metro station, Bengaluru (public demo start)


class FixtureSources:
    """Stands in for Sources: returns recorded responses, never touches the network."""

    offline = True

    def __init__(self, name="bengaluru_mg_road.json"):
        self.d = json.loads((FIX / name).read_text())
        self.calls = []

    def _f(self, k):
        self.calls.append(k)
        if k not in self.d:
            raise OfflineMiss(k)
        return Fetched(self.d[k], 0.0, True)

    def forecast(self, lat, lon):
        return self._f("forecast")

    def air(self, lat, lon):
        return self._f("air")

    def overpass(self, lat, lon, radius_m):
        return self._f("overpass")

    def overpass_features(self, lat, lon, radius_m):
        return self._f("overpass_features")

    def geocode(self, q):
        raise OfflineMiss("geocode")


def utc(hh, mm, day=11):
    """A UTC instant that is hh:mm IST on 2026-10-<day>."""
    total = hh * 60 + mm - 330
    d = day
    if total < 0:
        total += 1440
        d -= 1
    return datetime(2026, 10, d, total // 60, total % 60, tzinfo=timezone.utc)


@pytest.fixture
def src():
    return FixtureSources()
