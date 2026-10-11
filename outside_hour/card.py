"""Render a plan as a pocket card (plain text) and as a calendar reminder (.ics)."""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone


def text_card(p: dict, width: int = 56) -> str:
    w = textwrap.TextWrapper(width=width - 4)
    line = "+" + "-" * (width - 2) + "+"

    def row(s=""):
        out = []
        for chunk in (w.wrap(s) or [""]):
            out.append("| " + chunk.ljust(width - 4) + " |")
        return out

    rows = [line]
    rows += row(p["headline"].upper())
    rows += row()
    rows += row(f"{p['date']}  ·  leave {p['now']}  ·  turn back {p['turn_around']}  ·  back {p['back_by']}")
    rows += row()
    rows += row(p["route"])
    rows += row(p["why"])
    for wn in p["warnings"]:
        rows += row("! " + wn)
    rows += row()
    rows += row("NOTICE")
    for n in p["notice"]:
        rows += row("- " + n)
    rows += row()
    rows += row("BRING")
    for b in p["bring"]:
        rows += row("- " + b)
    rows += row()
    wx = p["weather"]
    aq = f", US AQI {wx['us_aqi']}" if wx.get("us_aqi") is not None else ""
    rows += row(f"{wx['sky']}, {wx['temp_c']:.0f}°C, rain {wx['rain_chance_pct']}%{aq}, sunset {wx['sunset']}")
    rows += row()
    rows += row(p["phone_down_line"])
    rows.append(line)
    rows += [f"  Words by: {p['writer']}"]
    for r in p.get("rejected", []):
        rows.append(f"  (model draft rejected: {r})")
    rows.append("  " + p["attribution"])
    return "\n".join(rows)


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 74:
            out.append(cur)
            cur = " "
        cur += ch
    out.append(cur)
    return "\r\n".join(out)


def _esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def ics(p: dict, date: datetime | None = None) -> str:
    """A calendar event with two alarms (turn back, home), so the phone's own clock app does the nagging."""
    date = date or datetime.fromisoformat(p["date"])
    start = datetime.combine(date.date(), datetime.strptime(p["now"], "%H:%M").time())
    turn = datetime.combine(date.date(), datetime.strptime(p["turn_around"], "%H:%M").time())
    end = datetime.combine(date.date(), datetime.strptime(p["back_by"], "%H:%M").time())
    if turn < start:
        turn += timedelta(days=1)
    if end < start:
        end += timedelta(days=1)
    f = "%Y%m%dT%H%M%S"
    before_end = int((end - turn).total_seconds() // 60)
    desc = "\\n".join([_esc(p["route"])] + [_esc("- " + n) for n in p["notice"]])
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//outside-hour//EN", "BEGIN:VEVENT",
        f"UID:outside-hour-{start.strftime(f)}@local",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(f)}Z",
        f"DTSTART:{start.strftime(f)}", f"DTEND:{end.strftime(f)}",
        _fold(f"SUMMARY:Outside: {_esc(p['place']['name'])}"),
        _fold(f"DESCRIPTION:{desc}"),
        "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Turn back now",
        f"TRIGGER;RELATED=END:-PT{before_end}M", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", "",
    ])
