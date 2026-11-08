"""Local solar position (NOAA approximation, ±1 min) — no network needed.

Used by the Sun & Moon shift: when the sun sets at the saved location, Orion warms the HUD, softens its voice and
holds non-critical alerts until morning.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional


def _jd(dt: datetime) -> float:
    dt = dt.astimezone(timezone.utc)
    return dt.timestamp() / 86400.0 + 2440587.5


def solar_elevation(lat: float, lon: float, when: Optional[datetime] = None) -> float:
    """Sun elevation in degrees above the horizon (negative = below)."""
    when = when or datetime.now(timezone.utc)
    jc = (_jd(when) - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    c = (math.sin(math.radians(m)) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
         + math.sin(math.radians(2 * m)) * (0.019993 - 0.000101 * jc) + math.sin(math.radians(3 * m)) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    obliq0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliq = obliq0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))
    y = math.tan(math.radians(obliq / 2)) ** 2
    eq_time = 4 * math.degrees(
        y * math.sin(2 * math.radians(l0)) - 2 * e * math.sin(math.radians(m))
        + 4 * e * y * math.sin(math.radians(m)) * math.cos(2 * math.radians(l0))
        - 0.5 * y * y * math.sin(4 * math.radians(l0)) - 1.25 * e * e * math.sin(2 * math.radians(m)))
    utc = when.astimezone(timezone.utc)
    minutes = utc.hour * 60 + utc.minute + utc.second / 60
    true_solar = (minutes + eq_time + 4 * lon) % 1440
    hour_angle = true_solar / 4 - 180 if true_solar / 4 >= 0 else true_solar / 4 + 180
    zenith = math.degrees(math.acos(
        math.sin(math.radians(lat)) * math.sin(math.radians(decl))
        + math.cos(math.radians(lat)) * math.cos(math.radians(decl)) * math.cos(math.radians(hour_angle))))
    return 90 - zenith


def next_crossing(lat: float, lon: float, start: datetime, rising: bool, step_min: int = 4,
                  horizon: float = -0.833) -> Optional[datetime]:
    """First time after `start` the sun crosses the horizon upward (rising) or downward (setting)."""
    prev = solar_elevation(lat, lon, start)
    t = start
    for _ in range(int(36 * 60 / step_min)):
        t2 = t + timedelta(minutes=step_min)
        cur = solar_elevation(lat, lon, t2)
        if (rising and prev < horizon <= cur) or (not rising and prev >= horizon > cur):
            return t2
        prev, t = cur, t2
    return None


def sun_state(lat: float, lon: float, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now().astimezone()
    elev = solar_elevation(lat, lon, now)
    start = now - timedelta(hours=14)
    sunset = next_crossing(lat, lon, start, rising=False)
    sunrise = next_crossing(lat, lon, now, rising=True)
    return {
        "elevation": round(elev, 1),
        "is_day": elev > -0.833,
        "sunset": sunset.astimezone().strftime("%H:%M") if sunset else None,
        "sunrise": sunrise.astimezone().strftime("%H:%M") if sunrise else None,
    }
