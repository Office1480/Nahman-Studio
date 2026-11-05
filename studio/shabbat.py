"""Rien n'est publié pendant Chabbat et Yom Tov, d'après le calendrier Hebcal."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import db
from .config import Settings


def parse_windows(items: list[dict]) -> list[tuple[datetime, datetime]]:
    """Fenêtres interdites : de l'allumage des bougies à la Havdala."""
    events = sorted((i for i in items if i.get("category") in ("candles", "havdalah")), key=lambda i: i["date"])
    windows, opened = [], None
    for item in events:
        moment = datetime.fromisoformat(item["date"])
        if item["category"] == "candles" and opened is None:
            opened = moment
        elif item["category"] == "havdalah" and opened is not None:
            windows.append((opened, moment))
            opened = None
    return windows


def fetch_windows(settings: Settings, http=httpx) -> list[tuple[datetime, datetime]]:
    start = date.today() - timedelta(days=2)
    windows = []
    for geoname in settings.hebcal_geoname_id.split(","):
        response = http.get("https://www.hebcal.com/hebcal", timeout=20, params={
            "v": 1, "cfg": "json", "maj": "on", "c": "on", "M": "on", "geo": "geoname", "geonameid": geoname.strip(),
            "start": start.isoformat(), "end": (start + timedelta(days=30)).isoformat()})
        response.raise_for_status()
        windows += parse_windows(response.json()["items"])
    return windows


def _windows(settings: Settings) -> list[tuple[datetime, datetime]] | None:
    cached = db.get_setting("hebcal_windows")
    if cached and cached["day"] == date.today().isoformat():
        return [(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in cached["windows"]]
    try:
        windows = fetch_windows(settings)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        db.log(f"Calendrier Hebcal indisponible : {exc}", "warning")
        return None
    db.set_setting("hebcal_windows", {"day": date.today().isoformat(),
                                      "windows": [(a.isoformat(), b.isoformat()) for a, b in windows]})
    return windows


def is_blocked(settings: Settings, at: datetime | None = None) -> bool:
    at = at or datetime.now(timezone.utc)
    windows = _windows(settings)
    if windows is None:
        # Sans calendrier, on reste prudent : pas de publication du vendredi midi à la fin du samedi.
        local = at.astimezone(ZoneInfo(settings.timezone))
        return (local.weekday() == 4 and local.hour >= 12) or local.weekday() == 5
    return any(start <= at <= end for start, end in windows)


def next_opening(settings: Settings) -> str | None:
    now = datetime.now(timezone.utc)
    windows = _windows(settings) or []
    ends = [end for start, end in windows if start <= now <= end]
    return max(ends).isoformat() if ends else None
