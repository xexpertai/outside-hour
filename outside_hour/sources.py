"""Keyless open-data sources, with a polite on-disk cache.

* OpenStreetMap via the Overpass API (places) and Nominatim (place-name search).
* Open-Meteo forecast and air-quality APIs (weather, sunset, AQI).

Every request sends a descriptive User-Agent, is spaced out (Nominatim: max one
per second), and is cached so the same plan never hits the servers twice.
In offline mode nothing touches the network: only cached responses are used.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import USER_AGENT

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# How long a cached answer counts as fresh (seconds).
TTL = {"overpass": 7 * 86400, "nominatim": 30 * 86400, "forecast": 3600, "air": 3600}


class OfflineMiss(RuntimeError):
    """Offline mode was asked for something that is not in the cache."""


@dataclass
class Fetched:
    data: dict | list
    fetched_at: float  # unix time when it actually came from the network
    from_cache: bool


def default_cache_dir() -> Path:
    env = os.environ.get("OUTSIDE_HOUR_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "outside-hour"


class Sources:
    def __init__(self, cache_dir: Path | None = None, offline: bool = False, timeout: float = 30):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.offline = offline
        self.timeout = timeout
        self._last_call: dict[str, float] = {}

    # ---- cache -------------------------------------------------------------
    def _key(self, kind: str, url: str, body: str | None) -> Path:
        h = hashlib.sha256(f"{url}\n{body or ''}".encode()).hexdigest()[:24]
        return self.cache_dir / kind / f"{h}.json"

    def _get(self, kind: str, url: str, body: str | None = None, min_gap: float = 1.0,
             cache_id: str | None = None) -> Fetched:
        path = self._key(kind, cache_id or url, body)
        cached = None
        if path.exists():
            cached = json.loads(path.read_text())
            fresh = time.time() - cached["fetched_at"] < TTL[kind]
            if fresh or self.offline:
                return Fetched(cached["data"], cached["fetched_at"], True)
        if self.offline:
            raise OfflineMiss(f"offline and no cached {kind} response")
        # Be polite: never fire two requests at the same service within min_gap.
        gap = time.time() - self._last_call.get(kind, 0)
        if gap < min_gap:
            time.sleep(min_gap - gap)
        try:
            data = self._http(url, body)
        except Exception:
            if cached:  # stale beats nothing; the card says how old it is
                return Fetched(cached["data"], cached["fetched_at"], True)
            raise
        finally:
            self._last_call[kind] = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        path.write_text(json.dumps({"url": url, "fetched_at": now, "data": data}))
        return Fetched(data, now, False)

    def _http(self, url: str, body: str | None):
        data = body.encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    # ---- services ----------------------------------------------------------
    def geocode(self, query: str) -> Fetched:
        url = NOMINATIM_URL + "?" + urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1})
        return self._get("nominatim", url, min_gap=1.1)

    def overpass(self, lat: float, lon: float, radius_m: int) -> Fetched:
        # Round the centre so nearby requests share one cache entry (~110 m grid).
        lat, lon = round(lat, 3), round(lon, 3)
        q = build_overpass_query(lat, lon, radius_m)
        body = urllib.parse.urlencode({"data": q})
        last_err: Exception | None = None
        # Public Overpass servers are busy and sometimes answer 429/504. Retry
        # slowly (5 s, then 15 s) and try a second instance; never hammer them.
        for attempt, url in enumerate(OVERPASS_URLS[:1] * 2 + OVERPASS_URLS[1:] + OVERPASS_URLS[:1]):
            if attempt:
                time.sleep(5 if attempt < 3 else 15)
            try:
                # Mirrors share one cache entry (cache_id), so offline mode finds it.
                return self._get("overpass", url, body, min_gap=2.0, cache_id="overpass")
            except OfflineMiss:
                raise
            except Exception as e:  # HTTP 429/504, timeouts, bad JSON
                last_err = e
        raise RuntimeError(f"Overpass unavailable: {last_err}")

    def overpass_features(self, lat: float, lon: float, radius_m: int, timeout: float = 12) -> Fetched:
        """Optional second query (benches, trees, taps, unnamed ponds). One short try: the plan works without
        it, so a busy Overpass server never holds a plan up for long."""
        lat, lon = round(lat, 3), round(lon, 3)
        body = urllib.parse.urlencode({"data": build_features_query(lat, lon, radius_m)})
        saved, self.timeout = self.timeout, timeout
        try:
            return self._get("overpass", OVERPASS_URLS[0], body, min_gap=2.0, cache_id="overpass-features")
        finally:
            self.timeout = saved

    def forecast(self, lat: float, lon: float) -> Fetched:
        params = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "hourly": "temperature_2m,apparent_temperature,precipitation_probability,wind_speed_10m,uv_index,weather_code",
            "daily": "sunrise,sunset",
            "timezone": "auto",
            "forecast_days": 2,
        }
        return self._get("forecast", FORECAST_URL + "?" + urllib.parse.urlencode(params))

    def air(self, lat: float, lon: float) -> Fetched:
        params = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "hourly": "us_aqi,pm2_5",
            "timezone": "auto",
            "forecast_days": 2,
        }
        return self._get("air", AIR_URL + "?" + urllib.parse.urlencode(params))


def build_overpass_query(lat: float, lon: float, radius_m: int) -> str:
    a = f"around:{int(radius_m)},{lat},{lon}"
    return (
        "[out:json][timeout:25];("
        f'nwr({a})["leisure"~"^(park|garden|nature_reserve)$"]["name"];'
        f'nwr({a})["tourism"="viewpoint"];'
        f'nwr({a})["natural"~"^(water|peak|wood|beach|tree_row)$"]["name"];'
        f'nwr({a})["landuse"="forest"]["name"];'
        f'way({a})["highway"~"^(path|footway|track)$"]["name"];'
        ");out bb tags;"
    )


def build_features_query(lat: float, lon: float, radius_m: int) -> str:
    """Unnamed things that make a place specific. Used only as "mapped here" features, never as places."""
    a = f"around:{int(radius_m)},{lat},{lon}"
    return (
        "[out:json][timeout:25];("
        f'nwr({a})["natural"="water"][!"name"];'
        f'nwr({a})["natural"~"^(wood|tree_row)$"][!"name"];'
        f'node({a})["natural"="tree"];'
        f'node({a})["amenity"~"^(bench|drinking_water|toilets)$"];'
        f'nwr({a})["leisure"="playground"];'
        ");out bb tags;"
    )
