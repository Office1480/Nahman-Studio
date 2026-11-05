"""Accès public à la chaîne via yt-dlp : inventaire et sous-titres, sans quota API.

À lancer depuis une connexion domestique : YouTube bloque souvent ces accès depuis un serveur.
Jamais de cookies du compte de la chaîne ici.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    # lang=fr : sinon YouTube renvoie parfois les titres traduits en anglais.
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "--extractor-args", "youtube:lang=fr", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def inventory(channel_id: str) -> list[dict]:
    videos = []
    for tab, kind in (("videos", "long"), ("streams", "live"), ("shorts", "short")):
        result = _run(["--flat-playlist", "-J", f"https://www.youtube.com/channel/{channel_id}/{tab}"], timeout=600)
        if result.returncode:
            raise RuntimeError(result.stderr[-500:])
        for entry in json.loads(result.stdout).get("entries", []):
            videos.append({"id": entry["id"], "title": entry.get("title") or "", "kind": kind,
                           "duration": int(entry.get("duration") or 0), "views": entry.get("view_count") or 0,
                           "thumbnail": f"https://i.ytimg.com/vi/{entry['id']}/hqdefault.jpg"})
    return videos


def latest_lives(channel_id: str, count: int) -> list[dict]:
    """Les derniers lives terminés, du plus récent au plus ancien."""
    result = _run(["--flat-playlist", "-J", "--playlist-end", str(count + 5),
                   f"https://www.youtube.com/channel/{channel_id}/streams"], timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr[-500:])
    entries = [e for e in json.loads(result.stdout).get("entries", [])
               if e.get("live_status") not in ("is_live", "is_upcoming")]
    return [{"id": e["id"], "title": e.get("title") or "", "kind": "live", "duration": int(e.get("duration") or 0),
             "views": e.get("view_count") or 0, "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg"}
            for e in entries[:count]]


def fetch(video_id: str) -> dict | None:
    """Métadonnées et sous-titres français d'une vidéo. Retourne None si aucun sous-titre n'existe encore."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(["-J", "--no-simulate", "--skip-download", "--write-auto-subs", "--write-subs",
                       "--sub-langs", "fr,fr-orig", "--sub-format", "vtt", "--sleep-subtitles", "2",
                       "-o", str(Path(tmp) / "s.%(ext)s"), f"https://www.youtube.com/watch?v={video_id}"])
        if result.returncode:
            raise RuntimeError(result.stderr[-500:])
        info = json.loads(result.stdout)
        files = sorted(Path(tmp).glob("*.vtt"), key=lambda p: "orig" in p.name)
        vtt = files[0].read_text(encoding="utf-8") if files else None
    date = info.get("upload_date") or ""
    return {"vtt": vtt, "description": info.get("description") or "", "tags": info.get("tags") or [],
            "duration": int(info.get("duration") or 0), "views": info.get("view_count") or 0,
            "title": info.get("title") or "", "live_status": info.get("live_status"),
            "published_at": f"{date[:4]}-{date[4:6]}-{date[6:]}T00:00:00Z" if date else None}
