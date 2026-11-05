"""API YouTube (Data v3 + Analytics) avec suivi du quota quotidien."""
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import db
from .config import Settings

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = "https://www.googleapis.com/auth/youtube.force-ssl https://www.googleapis.com/auth/yt-analytics.readonly"
COST = {"read": 1, "captions_list": 50, "captions_download": 200, "captions_insert": 400, "captions_update": 450,
        "videos_update": 50, "thumbnails_set": 50, "videos_insert": 1600}


class QuotaExceeded(Exception):
    pass


class YouTubeError(Exception):
    pass


def quota_day() -> str:
    # Le quota YouTube repart à zéro à minuit, heure du Pacifique.
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def spent() -> int:
    with db.connect() as c:
        row = c.execute("SELECT units FROM quota WHERE day=?", (quota_day(),)).fetchone()
    return row["units"] if row else 0


def record(units: int) -> None:
    with db.connect() as c:
        c.execute("INSERT INTO quota(day, units) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET units=units+?",
                  (quota_day(), units, units))


def can_spend(settings: Settings, units: int, archive: bool = False) -> bool:
    budget = settings.youtube_daily_quota - (settings.live_quota_reserve if archive else 0)
    return spent() + units <= budget


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    d, h, m, s = (int(x or 0) for x in match.groups())
    return d * 86400 + h * 3600 + m * 60 + s


def _multipart(metadata: dict, body: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    payload = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
               f"--{boundary}\r\nContent-Type: {content_type}\r\n\r\n").encode() + body + f"\r\n--{boundary}--".encode()
    return payload, f"multipart/related; boundary={boundary}"


class YouTube:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=120)
        self._token: str | None = None
        self._expires = datetime.min

    def _access_token(self) -> str:
        if self._token and datetime.now() < self._expires:
            return self._token
        response = self.http.post("https://oauth2.googleapis.com/token", data={
            "client_id": self.settings.google_client_id, "client_secret": self.settings.google_client_secret,
            "refresh_token": self.settings.google_refresh_token, "grant_type": "refresh_token"})
        if response.status_code != 200:
            raise YouTubeError(f"Autorisation Google refusée : {response.text[:200]}")
        data = response.json()
        self._token = data["access_token"]
        self._expires = datetime.now() + timedelta(seconds=data.get("expires_in", 3600) - 60)
        return self._token

    def _call(self, method: str, url: str, cost: str, archive: bool = False, **kwargs) -> httpx.Response:
        units = COST[cost]
        if not can_spend(self.settings, units, archive):
            raise QuotaExceeded(f"Quota du jour atteint ({spent()} unités)")
        headers = {"Authorization": f"Bearer {self._access_token()}", **kwargs.pop("headers", {})}
        response = self.http.request(method, url, headers=headers, **kwargs)
        record(units)
        if response.status_code == 403 and "quotaExceeded" in response.text:
            record(self.settings.youtube_daily_quota)
            raise QuotaExceeded("YouTube signale le quota atteint")
        if response.status_code >= 400:
            raise YouTubeError(f"{method} {url.split('?')[0]} : {response.status_code} {response.text[:300]}")
        return response

    def details(self, ids: list[str]) -> list[dict]:
        videos = []
        for i in range(0, len(ids), 50):
            items = self._call("GET", f"{API}/videos", "read", params={
                "part": "snippet,contentDetails,statistics,liveStreamingDetails", "id": ",".join(ids[i:i + 50]),
                "maxResults": 50}).json()["items"]
            videos += [self._video(item) for item in items]
        return videos

    def _video(self, item: dict) -> dict:
        snippet = item["snippet"]
        duration = parse_duration(item["contentDetails"].get("duration"))
        live = "liveStreamingDetails" in item
        thumbs = snippet.get("thumbnails", {})
        best = thumbs.get("maxres") or thumbs.get("high") or thumbs.get("default") or {}
        kind = "live" if live else ("short" if duration <= 180 and self._is_short(item["id"]) else "long")
        return {"id": item["id"], "title": snippet["title"], "description": snippet.get("description", ""),
                "tags": snippet.get("tags", []), "published_at": snippet.get("publishedAt"), "duration": duration,
                "kind": kind, "live_ended": int(snippet.get("liveBroadcastContent") == "none"),
                "views": int(item.get("statistics", {}).get("viewCount", 0)), "thumbnail": best.get("url", "")}

    def _is_short(self, video_id: str) -> bool:
        response = self.http.head(f"https://www.youtube.com/shorts/{video_id}", follow_redirects=False)
        return response.status_code == 200

    def caption_tracks(self, video_id: str) -> list[dict]:
        items = self._call("GET", f"{API}/captions", "captions_list",
                           params={"part": "snippet", "videoId": video_id}).json()["items"]
        return [{"id": t["id"], **t["snippet"]} for t in items]

    def download_caption(self, track_id: str, archive: bool = False) -> str:
        return self._call("GET", f"{API}/captions/{track_id}", "captions_download", archive,
                          params={"tfmt": "vtt"}).text

    def upsert_caption(self, video_id: str, name: str, srt: str, archive: bool = False) -> None:
        existing = next((t for t in self.caption_tracks(video_id) if t.get("name") == name), None)
        if existing:
            metadata, cost, method = {"id": existing["id"]}, "captions_update", "PUT"
        else:
            metadata = {"snippet": {"videoId": video_id, "language": "fr", "name": name, "isDraft": False}}
            cost, method = "captions_insert", "POST"
        body, content_type = _multipart(metadata, srt.encode("utf-8"), "application/octet-stream")
        self._call(method, f"{UPLOAD}/captions", cost, archive, params={"part": "snippet", "uploadType": "multipart"},
                   content=body, headers={"Content-Type": content_type})

    def update_metadata(self, video_id: str, title: str, description: str, tags: list[str],
                        localizations: dict | None = None, archive: bool = False) -> None:
        item = self._call("GET", f"{API}/videos", "read", params={"part": "snippet,localizations", "id": video_id}).json()["items"][0]
        snippet = item["snippet"]
        snippet.update(title=title, description=description, tags=tags, defaultLanguage="fr")
        parts = "snippet"
        body = {"id": video_id, "snippet": {k: snippet[k] for k in ("title", "description", "tags", "categoryId",
                                                                    "defaultLanguage")}}
        if localizations:
            parts = "snippet,localizations"
            body["localizations"] = {**item.get("localizations", {}), **localizations}
        self._call("PUT", f"{API}/videos", "videos_update", archive, params={"part": parts}, json=body)

    def set_thumbnail(self, video_id: str, png: bytes, archive: bool = False) -> None:
        self._call("POST", f"{UPLOAD}/thumbnails/set", "thumbnails_set", archive,
                   params={"videoId": video_id, "uploadType": "media"}, content=png,
                   headers={"Content-Type": "image/png"})

    def upload_video(self, path: str, title: str, description: str, tags: list[str]) -> str:
        """Publie une vidéo, en envoi par morceaux pour ne pas charger le fichier en mémoire."""
        size = os.path.getsize(path)
        metadata = {"snippet": {"title": title[:100], "description": description, "tags": tags, "categoryId": "27",
                                "defaultLanguage": "fr"},
                    "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
        session = self._call("POST", f"{UPLOAD}/videos", "videos_insert", True,
                             params={"part": "snippet,status", "uploadType": "resumable"}, json=metadata,
                             headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
        with open(path, "rb") as handle:
            response = self.http.put(session.headers["Location"], content=handle, timeout=3600, headers={
                "Authorization": f"Bearer {self._access_token()}", "Content-Type": "video/mp4",
                "Content-Length": str(size)})
        if response.status_code >= 400:
            raise YouTubeError(f"Envoi de la vidéo : {response.status_code} {response.text[:300]}")
        return response.json()["id"]

    def top_videos(self, days: int = 90, limit: int = 200) -> list[dict]:
        """Vidéos les plus vues sur la période (l'API Analytics n'a pas de coût de quota Data)."""
        end = date.today()
        response = self.http.get("https://youtubeanalytics.googleapis.com/v2/reports", params={
            "ids": "channel==MINE", "startDate": (end - timedelta(days=days)).isoformat(), "endDate": end.isoformat(),
            "metrics": "views,estimatedMinutesWatched,averageViewPercentage", "dimensions": "video",
            "sort": "-views", "maxResults": limit}, headers={"Authorization": f"Bearer {self._access_token()}"})
        if response.status_code >= 400:
            raise YouTubeError(f"Analytics : {response.status_code} {response.text[:200]}")
        data = response.json()
        names = [h["name"] for h in data["columnHeaders"]]
        return [dict(zip(names, row)) for row in data.get("rows", [])]


def authorize_url(settings: Settings, redirect: str) -> str:
    return str(httpx.URL("https://accounts.google.com/o/oauth2/v2/auth", params={
        "client_id": settings.google_client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent"}))


def exchange_code(settings: Settings, code: str, redirect: str) -> str:
    response = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "code": code,
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    response.raise_for_status()
    return response.json()["refresh_token"]
