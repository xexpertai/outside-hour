"""Summarise Open-Meteo forecast + air quality for the time window you'll be outside."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# WMO weather codes → short words (https://open-meteo.com/en/docs)
WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


@dataclass
class Window:
    now: datetime  # local, naive
    tz_name: str
    sunrise: datetime | None
    sunset: datetime | None
    temp_c: float
    feels_c: float
    rain_pct: int
    wind_kmh: float
    uv: float
    sky: str
    aqi: int | None
    pm25: float | None
    thunder: bool

    @property
    def wet(self) -> bool:
        return self.rain_pct >= 40 or "rain" in self.sky or "drizzle" in self.sky or self.thunder


def local_now(forecast: dict, utc_now: datetime | None = None) -> datetime:
    utc_now = utc_now or datetime.now(timezone.utc)
    off = timedelta(seconds=forecast.get("utc_offset_seconds", 0))
    return (utc_now + off).replace(tzinfo=None)


def _rows(block: dict, start: datetime, end: datetime) -> list[dict]:
    times = [datetime.fromisoformat(t) for t in block["time"]]
    out = []
    for i, t in enumerate(times):
        # include the hour that contains `start` through the hour that contains `end`
        if t + timedelta(hours=1) > start and t <= end:
            out.append({k: v[i] for k, v in block.items() if k != "time"})
    return out


def summarise(forecast: dict, air: dict | None, now: datetime, minutes: int) -> Window:
    end = now + timedelta(minutes=minutes)
    rows = _rows(forecast["hourly"], now, end) or _rows(forecast["hourly"], now - timedelta(hours=1), now)

    def vals(k):
        return [r[k] for r in rows if r.get(k) is not None]

    codes = vals("weather_code")
    worst = max(codes) if codes else 0
    sunrise = sunset = None
    for sr, ss in zip(forecast["daily"]["sunrise"], forecast["daily"]["sunset"]):
        sr, ss = datetime.fromisoformat(sr), datetime.fromisoformat(ss)
        if sr.date() == now.date():
            sunrise, sunset = sr, ss
    aqi = pm = None
    if air:
        arows = _rows(air["hourly"], now, end)
        a = [r["us_aqi"] for r in arows if r.get("us_aqi") is not None]
        p = [r["pm2_5"] for r in arows if r.get("pm2_5") is not None]
        aqi = int(round(max(a))) if a else None
        pm = round(max(p), 1) if p else None
    return Window(
        now=now,
        tz_name=forecast.get("timezone", "local"),
        sunrise=sunrise,
        sunset=sunset,
        temp_c=round(max(vals("temperature_2m") or [0]), 0),
        feels_c=round(max(vals("apparent_temperature") or vals("temperature_2m") or [0]), 0),
        rain_pct=int(max(vals("precipitation_probability") or [0])),
        wind_kmh=round(max(vals("wind_speed_10m") or [0]), 0),
        uv=round(max(vals("uv_index") or [0]), 0),
        sky=WMO.get(worst, "mixed"),
        aqi=aqi,
        pm25=pm,
        thunder=worst >= 95,
    )
