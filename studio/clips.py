"""Extraits : histoires, Shorts et moments clés, de la détection à la publication."""
import glob
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import captions, db, glossary, ingest, search, shabbat, telegram, youtube
from .config import Settings

KINDS = ("histoire", "short", "moment")
LIMITS = {"histoire": (20, 1800), "short": (15, 180), "moment": (45, 900)}
POST_PER_PASS = 3
CLAIM_HOURS = 2
_busy = threading.Lock()


def default_format(kind: str) -> str:
    return "vertical" if kind == "short" else "horizontal"


def snap(cues: list[dict], start: float, end: float) -> tuple[float, float]:
    """Cale le début et la fin sur des lignes de sous-titres, pour ne pas couper un mot."""
    before = [c["start"] for c in cues if c["start"] <= start]
    after = [c["end"] for c in cues if c["end"] >= end]
    return (max(before) if before else start), (min(after) if after else end)


def add(video_id: str, kind: str, items: list[dict], duration: int = 0) -> int:
    """Enregistre des extraits proposés, sans doublon avec ceux déjà connus de la même sorte."""
    cues = ingest.video_cues(video_id)
    low, high = LIMITS[kind]
    with db.connect() as c:
        known = [(r["start"], r["end"]) for r in c.execute("SELECT start, end FROM clips WHERE video_id=? AND kind=?",
                                                           (video_id, kind))]
    added = 0
    for item in items:
        start, end = snap(cues, item["start"], item["end"])
        if duration:
            end = min(end, duration)
        if not low <= end - start <= high or not item["title"].strip():
            continue
        overlaps = [min(end, b) - max(start, a) for a, b in known]
        if any(o > 0.5 * (end - start) for o in overlaps):
            continue
        with db.connect() as c:
            c.execute("INSERT INTO clips(video_id, kind, start, end, title, summary, hook, format, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (video_id, kind, start, end, item["title"].strip()[:100], item.get("summary", ""),
                       item.get("hook", ""), default_format(kind), db.now()))
        known.append((start, end))
        added += 1
    return added


def store_detection(video_id: str, payload: dict, duration: int = 0) -> int:
    total = sum(add(video_id, kind, payload[key], duration)
                for kind, key in (("histoire", "stories"), ("moment", "moments"), ("short", "shorts")))
    with db.connect() as c:
        c.execute("UPDATE videos SET clips_status='done' WHERE id=?", (video_id,))
    return total


def _view(row) -> dict:
    clip = dict(row)
    clip["length"] = int(clip["end"] - clip["start"])
    clip["time"] = captions.timestamp(clip["start"])
    clip["url"] = search.video_url(clip["video_id"], clip["start"])
    clip["posted_url"] = f"https://youtu.be/{clip['youtube_id']}" if clip["youtube_id"] else ""
    clip["has_file"] = bool(clip["file"]) and Path(clip["file"]).exists()
    clip.pop("file")
    return clip


def listing(kind: str = "", status: str = "proposed", query: str = "", limit: int = 60, offset: int = 0) -> dict:
    where, params = ["1=1"], []
    if status == "active":
        where.append("c.status IN ('validated','rendering','rendered','uploading','failed')")
    elif status:
        where.append("c.status=?")
        params.append(status)
    if kind:
        where.append("c.kind=?")
        params.append(kind)
    if query:
        where.append("(c.title LIKE ? OR c.summary LIKE ? OR v.title LIKE ?)")
        params += [f"%{query}%"] * 3
    clause = " AND ".join(where)
    source = "FROM clips c JOIN videos v ON v.id=c.video_id"
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {source} WHERE {clause}", params).fetchone()[0]
        rows = conn.execute(f"SELECT c.*, v.title AS lesson, v.thumbnail, v.views {source} WHERE {clause} "
                            "ORDER BY v.published_at DESC, c.video_id, c.start LIMIT ? OFFSET ?",
                            (*params, limit, offset)).fetchall()
        kinds = {r["kind"]: r["n"] for r in conn.execute("SELECT kind, COUNT(*) n FROM clips WHERE status='proposed' GROUP BY kind")}
        states = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM clips GROUP BY status")}
        scanned = conn.execute("SELECT COUNT(*) FROM videos WHERE clips_status='done'").fetchone()[0]
    return {"clips": [_view(r) for r in rows], "total": total, "kinds": kinds, "states": states, "lessons": scanned}


def get(clip_id: int) -> dict | None:
    with db.connect() as c:
        row = c.execute("SELECT c.*, v.title AS lesson, v.thumbnail, v.views FROM clips c "
                        "JOIN videos v ON v.id=c.video_id WHERE c.id=?", (clip_id,)).fetchone()
    return _view(row) if row else None


def excerpt(clip: dict) -> str:
    cues = ingest.video_cues(clip["video_id"])
    return " ".join(c["text"] for c in cues if c["end"] > clip["start"] and c["start"] < clip["end"])


def validate(clip_id: int, fields: dict | None = None) -> None:
    fields = fields or {}
    allowed = {k: fields[k] for k in ("title", "summary", "hook", "start", "end", "format") if k in fields}
    if "title" in allowed:
        allowed["title"] = allowed["title"].strip()[:100]
    if allowed.get("format") not in (None, "vertical", "horizontal"):
        raise ValueError("Format inconnu")
    if "start" in allowed and "end" in allowed and allowed["end"] - allowed["start"] < 5:
        raise ValueError("L'extrait est trop court")
    if allowed.get("format") == "vertical" and allowed.get("end", 0) - allowed.get("start", 0) > 180:
        raise ValueError("Un Short ne peut pas dépasser 3 minutes")
    sets = ", ".join(f"{k}=?" for k in allowed)
    with db.connect() as c:
        c.execute(f"UPDATE clips SET {sets + ', ' if sets else ''}status='validated', decided_at=?, error='' "
                  "WHERE id=? AND status IN ('proposed','failed')", (*allowed.values(), db.now(), clip_id))


def reject(clip_id: int) -> None:
    with db.connect() as c:
        c.execute("UPDATE clips SET status='rejected', decided_at=? WHERE id=? AND status IN ('proposed','failed')",
                  (db.now(), clip_id))


def reopen(clip_id: int) -> None:
    with db.connect() as c:
        c.execute("UPDATE clips SET status='proposed', error='' WHERE id=? AND status='rejected'", (clip_id,))


# --- Fabrication -------------------------------------------------------------------------------------------------

STYLES = {
    "vertical": {"size": (1080, 1920), "sub": (62, 420), "hook": (70, 190)},
    "horizontal": {"size": (1280, 720), "sub": (40, 40), "hook": (46, 40)},
}


def _time(value: float) -> str:
    return f"{int(value // 3600)}:{int(value % 3600 // 60):02d}:{value % 60:05.2f}"


def build_ass(cues: list[dict], start: float, end: float, hook: str, shape: str) -> str:
    style = STYLES[shape]
    width, height = style["size"]
    (sub_size, sub_margin), (hook_size, hook_margin) = style["sub"], style["hook"]
    fields = "Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV"
    lines = [f"""[Script Info]
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, {fields}
Style: Sub,Arial,{sub_size},&H00FFFFFF,&H00000000,&H00000000,-1,1,4,0,2,60,60,{sub_margin}
Style: Hook,Arial,{hook_size},&H005CD6FF,&H00000000,&H00000000,-1,1,4,0,8,60,60,{hook_margin}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""]
    if hook:
        clean = hook.replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,0:00:00.00,{_time(min(end - start, 6))},Hook,,0,0,0,,{clean}\n")
    timed = [c for c in cues if c["end"] > start and c["start"] < end]
    for i, cue in enumerate(timed):
        # Chaque ligne reste affichée jusqu'à la suivante.
        until = timed[i + 1]["start"] if i + 1 < len(timed) else end
        begin = max(cue["start"], start) - start
        text = cue["text"].replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{_time(begin)},{_time(min(until, end) - start)},Sub,,0,0,0,,{text}\n")
    return "".join(lines)


def _ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        pattern = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe")
        found = next(iter(glob.glob(pattern)), None)
    if not found:
        raise RuntimeError("ffmpeg n'est pas installé")
    return found


class BotCheck(RuntimeError):
    """YouTube demande une vérification anti-robots : on ne la contourne pas, on réessaie plus tard."""


def render(settings: Settings, clip: dict, cues: list[dict] | None = None) -> Path:
    """Télécharge le passage et fabrique la vidéo finale. Il faut une connexion domestique pour yt-dlp."""
    ffmpeg = _ffmpeg()
    cues = ingest.video_cues(clip["video_id"]) if cues is None else cues
    work = settings.data_dir / "clips" / str(clip["id"])
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    start, end = clip["start"], clip["end"]
    local = settings.data_dir / "sources" / f"{clip['video_id']}.mp4"
    if local.exists():
        # Fichier d'origine (YouTube Studio, enregistrement local) : la voie sûre, sans passer par yt-dlp.
        subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", str(start), "-to", str(end), "-i", str(local),
                        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(work / "source.mp4")],
                       check=True, capture_output=True, timeout=1800)
    else:
        download = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--no-warnings", "-f", "b[height<=720]/bv*[height<=720]+ba/b",
             "--merge-output-format", "mp4", "--ffmpeg-location", str(Path(ffmpeg).parent),
             "--download-sections", f"*{start}-{end}", "-o", str(work / "source.%(ext)s"),
             f"https://www.youtube.com/watch?v={clip['video_id']}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if download.returncode:
            if "not a bot" in download.stderr:
                raise BotCheck("YouTube a demandé une vérification anti-robots. Réessaie plus tard, ou dépose le "
                               f"fichier du cours dans data/sources/{clip['video_id']}.mp4.")
            reason = next((x for x in download.stderr.splitlines() if x.startswith("ERROR")), download.stderr[-200:])
            raise RuntimeError(f"Téléchargement refusé : {reason[:300]}")

    probe = subprocess.run([str(Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))), "-v", "error",
                            "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                            "source.mp4"], cwd=work, capture_output=True, text=True, timeout=60)
    width, height = (int(x) for x in probe.stdout.strip().split("x")[:2])
    shape = clip["format"]
    hook = clip["hook"] if shape == "vertical" else clip["title"]
    # Les lives récents sont filmés à la verticale : les sous-titres suivent l'image réelle, pas le format demandé.
    style = "vertical" if shape == "vertical" or height > width else "horizontal"
    (work / "subs.ass").write_text(build_ass(cues, start, end, hook, style), encoding="utf-8")
    if shape == "vertical":
        graph = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30[bg];"
                 "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles=subs.ass[v]")
    else:
        graph = "[0:v]subtitles=subs.ass[v]"
    result = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", "source.mp4", "-filter_complex", graph, "-map", "[v]",
                             "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
                             "-t", str(end - start), "clip.mp4"],
                            cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    if result.returncode:
        raise RuntimeError(f"Montage impossible : {result.stderr[-200:]}")
    (work / "source.mp4").unlink(missing_ok=True)
    return work / "clip.mp4"


def description(settings: Settings, clip: dict) -> str:
    """Résumé, lien vers le cours complet à la bonne minute, puis le bloc habituel de la chaîne."""
    with db.connect() as c:
        backup = c.execute("SELECT description FROM backups WHERE video_id=?", (clip["video_id"],)).fetchone()
        video = c.execute("SELECT description, title FROM videos WHERE id=?", (clip["video_id"],)).fetchone()
    block = (backup["description"] if backup else video["description"]) or ""
    parts = [clip["summary"], f"Le cours complet : {clip['url']}\n{video['title']}", block]
    if clip["format"] == "vertical":
        parts.append("#Shorts #Breslev")
    return "\n\n".join(p for p in parts if p)[:4900]


def _set(clip_id: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.connect() as c:
        c.execute(f"UPDATE clips SET {sets} WHERE id=?", (*fields.values(), clip_id))


def claim_next(settings: Settings) -> dict | None:
    """Donne le prochain extrait validé au programme du PC, avec ses sous-titres. Rien pendant Chabbat."""
    db.set_setting("agent_seen", db.now())
    if shabbat.is_blocked(settings):
        return None
    stale = (datetime.now(timezone.utc) - timedelta(hours=CLAIM_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.connect() as c:
        c.execute("UPDATE clips SET status='validated' WHERE status='rendering' AND claimed_at<?", (stale,))
        row = c.execute("SELECT id FROM clips WHERE status='validated' ORDER BY decided_at LIMIT 1").fetchone()
        if not row:
            return None
        c.execute("UPDATE clips SET status='rendering', claimed_at=? WHERE id=?", (db.now(), row["id"]))
    clip = get(row["id"])
    return {**clip, "cues": [c for c in ingest.video_cues(clip["video_id"])
                             if c["end"] > clip["start"] and c["start"] < clip["end"]]}


def complete(clip_id: int, path: Path) -> None:
    _set(clip_id, status="rendered", file=str(path), error="")


def agent_failed(clip_id: int, error: str, retry: bool) -> None:
    _set(clip_id, status="validated" if retry else "failed", error=error[:400])


def agent_status(settings: Settings) -> dict:
    seen = db.get_setting("agent_seen")
    age = None
    if seen:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(seen.replace("Z", "+00:00"))).total_seconds()
    return {"enabled": bool(settings.agent_token), "online": age is not None and age < 180, "last_seen": seen}


def process(settings: Settings, api: "youtube.YouTube | None" = None, make=render) -> None:
    """Extrait puis publie les extraits validés. Une seule passe à la fois, jamais pendant Chabbat."""
    if not _busy.acquire(blocking=False):
        return
    try:
        if shabbat.is_blocked(settings):
            return
        # Avec le programme du PC, le serveur ne fait que publier ce qu'il a reçu.
        statuses = "('rendered')" if settings.agent_token else "('validated','rendered')"
        with db.connect() as c:
            ids = [r["id"] for r in c.execute(f"SELECT id FROM clips WHERE status IN {statuses} "
                                              "ORDER BY decided_at LIMIT ?", (POST_PER_PASS,))]
        for clip_id in ids:
            if not _advance(settings, clip_id, api, make):
                break
    finally:
        _busy.release()


def _advance(settings: Settings, clip_id: int, api, make) -> bool:
    clip = get(clip_id)
    try:
        if clip["status"] == "validated":
            _set(clip_id, status="rendering")
            path = make(settings, clip)
            _set(clip_id, status="rendered", file=str(path), error="")
        if not settings.has_youtube:
            return True
        if not youtube.can_spend(settings, youtube.COST["videos_insert"], archive=True):
            db.log("Quota du jour atteint : les extraits reprendront demain", "warning")
            return False
        with db.connect() as c:
            path = c.execute("SELECT file FROM clips WHERE id=?", (clip_id,)).fetchone()["file"]
        _set(clip_id, status="uploading")
        client = api or youtube.YouTube(settings)
        tags = ["Breslev", "Rabbi Nahman", "Nathan Uzan"]
        video_id = client.upload_video(path, clip["title"], description(settings, clip), tags)
        _set(clip_id, status="posted", youtube_id=video_id, posted_at=db.now(), error="", file="")
        Path(path).unlink(missing_ok=True)
        db.log(f"Extrait publié : {clip['title']}")
        if settings.has_telegram:
            telegram.Bot(settings).send(f"Extrait publié : <b>{clip['title']}</b>\nhttps://youtu.be/{video_id}")
    except youtube.QuotaExceeded as exc:
        _set(clip_id, status="rendered")
        db.log(str(exc), "warning")
        return False
    except Exception as exc:
        _set(clip_id, status="failed", error=str(exc)[:400])
        db.log(f"Extrait « {clip['title']} » : {str(exc)[:200]}", "error")
    return True
