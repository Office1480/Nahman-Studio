"""La chaîne automatique : détection, sous-titres, IA, validation, publication, diffusion."""
import json
import threading
import traceback
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import ai, captions, db, embeddings, ingest, search, shabbat, telegram, thumbnails, youtube, ytdlp_source
from .config import Settings

CAPTION_TRACK = "Français (corrigé)"
GIVE_UP_HOURS = 72
EXPERIMENT_SIZE = 200
EXPERIMENT_DAYS = 28


def _video(video_id: str) -> dict:
    with db.connect() as c:
        row = dict(c.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())
    row["tags"] = json.loads(row["tags"] or "[]")
    return row


def _notify(settings: Settings, text: str, buttons=None) -> None:
    if settings.has_telegram:
        telegram.Bot(settings).send(text, buttons)


def _age_hours(stamp: str | None) -> float:
    if not stamp:
        return 1e9
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - moment).total_seconds() / 3600


# --- Détection ---------------------------------------------------------------------------------------------------

def rss_videos(channel_id: str, http=httpx) -> list[dict]:
    """Les 15 dernières vidéos, par le flux public de la chaîne : gratuit, sans clé, sans quota."""
    response = http.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id}, timeout=30)
    response.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    videos = []
    for entry in ET.fromstring(response.content).findall("a:entry", ns):
        video_id = entry.find("yt:videoId", ns).text
        videos.append({"id": video_id, "title": entry.find("a:title", ns).text or "",
                       "published_at": entry.find("a:published", ns).text,
                       "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"})
    return videos


def discover(settings: Settings) -> list[str]:
    feed = rss_videos(settings.channel_id)
    with db.connect() as c:
        known = {r["id"] for r in c.execute("SELECT id FROM videos")}
    fresh = [v for v in feed if v["id"] not in known]
    if not fresh:
        return []
    if not known:
        # Premier lancement sans inventaire : ce sont des archives, pas des nouveautés.
        for video in fresh:
            ingest.upsert_video(video, is_archive=True)
        return []
    details = {}
    if settings.has_youtube:
        details = {v["id"]: v for v in youtube.YouTube(settings).details([v["id"] for v in fresh])}
    for video in fresh:
        ingest.upsert_video({**video, **details.get(video["id"], {})})
        db.log(f"Nouvelle vidéo détectée : {video['title']}")
        _notify(settings, f"Nouvelle vidéo détectée : <b>{video['title']}</b>\nJ'attends ses sous-titres.")
    return [v["id"] for v in fresh]


# --- Sous-titres -------------------------------------------------------------------------------------------------

def pick_track(tracks: list[dict]) -> dict | None:
    """Sous-titres français prêts, les manuels d'abord, jamais notre propre piste corrigée."""
    usable = [t for t in tracks if t.get("language", "").startswith("fr") and t.get("status") == "serving"
              and t.get("name") != CAPTION_TRACK]
    usable.sort(key=lambda t: t.get("trackKind") == "asr")
    return usable[0] if usable else None


def fetch_captions(settings: Settings, video_id: str, archive: bool = False) -> bool:
    """Récupère et stocke le transcript s'il existe. Retourne True s'il est disponible."""
    if settings.has_youtube:
        api = youtube.YouTube(settings)
        track = pick_track(api.caption_tracks(video_id))
        if not track:
            return False
        ingest.store_transcript(video_id, api.download_caption(track["id"], archive), f"api:{track.get('trackKind')}")
        return True
    info = ytdlp_source.fetch(video_id)
    fields = {k: info[k] for k in ("title", "description", "tags", "published_at", "views", "duration")}
    if info["live_status"] in ("is_live", "is_upcoming"):
        return False
    if 0 < info["duration"] <= 180:
        fields["kind"] = "short"
    ingest.upsert_video({"id": video_id, **fields})
    if not info["vtt"]:
        return False
    ingest.store_transcript(video_id, info["vtt"], "yt-dlp")
    return True


def check_captions(settings: Settings) -> None:
    if settings.has_youtube:
        with db.connect() as c:
            running = [r["id"] for r in c.execute("SELECT id FROM videos WHERE live_ended=0")]
        for video in youtube.YouTube(settings).details(running) if running else []:
            ingest.upsert_video(video)
    with db.connect() as c:
        rows = c.execute("SELECT * FROM videos WHERE caption_status='pending' AND is_archive=0 AND live_ended=1"
                         ).fetchall()
    for video in rows:
        if _age_hours(video["caption_checked_at"]) * 60 < settings.caption_check_minutes:
            continue
        try:
            available = fetch_captions(settings, video["id"])
        except Exception as exc:
            # Une vidéo en échec ne doit pas bloquer les suivantes ; elle sera retentée au prochain passage.
            db.log(f"Sous-titres de {video['title']} : {str(exc)[:200]}", "warning")
            available = False
        with db.connect() as c:
            c.execute("UPDATE videos SET caption_checked_at=? WHERE id=?", (db.now(), video["id"]))
        if available:
            db.log(f"Sous-titres récupérés : {video['title']}")
        elif _age_hours(video["created_at"]) > GIVE_UP_HOURS:
            with db.connect() as c:
                c.execute("UPDATE videos SET caption_status='none' WHERE id=?", (video["id"],))
            db.log(f"Pas de sous-titres après {GIVE_UP_HOURS} h : {video['title']}", "warning")
            _notify(settings, f"Toujours pas de sous-titres après {GIVE_UP_HOURS} h pour <b>{video['title']}</b>.")


# --- Génération --------------------------------------------------------------------------------------------------

def store_analysis(video_id: str, payload: dict) -> None:
    """Rend les chapitres, sources et concepts disponibles pour la recherche."""
    with db.connect() as c:
        for table in ("chapters", "refs", "concepts"):
            c.execute(f"DELETE FROM {table} WHERE video_id=?", (video_id,))
        c.executemany("INSERT INTO chapters VALUES (?,?,?)",
                      [(video_id, x["start"], x["title"]) for x in payload["chapters"]])
        c.executemany("INSERT INTO refs VALUES (?,?,?,?,?)",
                      [(video_id, x["start"], x["book"], x["ref"], x["label"]) for x in payload["sources"]])
        c.executemany("INSERT INTO concepts VALUES (?,?,?)",
                      [(video_id, x["start"], x["concept"]) for x in payload["concepts"]])


def create_proposal(settings: Settings, video_id: str, payload: dict, notify: bool = True) -> int:
    video = _video(video_id)
    payload = ai.normalize(payload, video["duration"])
    ingest.apply_corrections(video_id, payload["corrections"])
    store_analysis(video_id, payload)
    kind = "archive" if video["is_archive"] else "live"
    with db.connect() as c:
        c.execute("UPDATE proposals SET status='rejected' WHERE video_id=? AND status='pending'", (video_id,))
        cursor = c.execute("INSERT INTO proposals(video_id, kind, payload, created_at) VALUES (?,?,?,?)",
                           (video_id, kind, json.dumps(payload, ensure_ascii=False), db.now()))
        c.execute("UPDATE videos SET ai_status='done', ai_error='' WHERE id=?", (video_id,))
    proposal_id = cursor.lastrowid
    db.log(f"Proposition prête : {video['title']}")
    if notify and kind == "live" and settings.has_telegram:
        text, buttons = telegram.proposal_message(settings, proposal_id, video, payload)
        _notify(settings, text, buttons)
    return proposal_id


def run_ai(settings: Settings) -> None:
    if not settings.has_claude:
        return
    with db.connect() as c:
        rows = c.execute("SELECT id FROM videos WHERE caption_status='fetched' AND kind!='short' AND "
                         "((ai_status='todo' AND is_archive=0) OR ai_status='regen')").fetchall()
    for row in rows:
        video_id = row["id"]
        video = _video(video_id)
        note = db.get_setting(f"regen_note:{video_id}", "")
        try:
            payload = ai.generate(settings, video, ingest.video_passages(video_id), note)
            create_proposal(settings, video_id, payload)
        except Exception as exc:
            with db.connect() as c:
                c.execute("UPDATE videos SET ai_status='failed', ai_error=? WHERE id=?", (str(exc)[:500], video_id))
            db.log(f"Échec IA sur {video['title']} : {exc}", "error")


# --- Archives ----------------------------------------------------------------------------------------------------

def setup_experiment() -> None:
    """Les plus vues sont réparties en deux groupes : un traité, un témoin laissé intact pendant 4 semaines."""
    if db.get_setting("experiment_started_at"):
        return
    with db.connect() as c:
        rows = c.execute("SELECT id, views FROM videos WHERE is_archive=1 AND kind!='short' "
                         "ORDER BY views DESC LIMIT ?", (EXPERIMENT_SIZE,)).fetchall()
        for rank, row in enumerate(rows):
            c.execute("INSERT OR IGNORE INTO experiment(video_id, grp, views_start, started_at, views_last, measured_at)"
                      " VALUES (?,?,?,?,?,?)", (row["id"], "traité" if rank % 2 == 0 else "témoin", row["views"],
                                                db.now(), row["views"], db.now()))
    db.set_setting("experiment_started_at", db.now())


def archive_candidates(limit: int) -> list[str]:
    started = db.get_setting("experiment_started_at")
    hold_control = started and _age_hours(started) < EXPERIMENT_DAYS * 24
    exclude = "AND id NOT IN (SELECT video_id FROM experiment WHERE grp='témoin')" if hold_control else ""
    with db.connect() as c:
        rows = c.execute(f"SELECT id FROM videos WHERE is_archive=1 AND kind!='short' AND ai_status='todo' {exclude} "
                         "AND caption_status IN ('pending','fetched') ORDER BY views DESC LIMIT ?", (limit,)).fetchall()
    return [r["id"] for r in rows]


def run_archive(settings: Settings) -> None:
    if not settings.has_claude or not db.get_setting("archive_enabled", False):
        return
    batch_id = db.get_setting("archive_batch")
    if batch_id:
        results = ai.collect_batch(settings, batch_id)
        if results is None:
            return
        ready = 0
        for video_id, result in results.items():
            if isinstance(result, Exception):
                with db.connect() as c:
                    c.execute("UPDATE videos SET ai_status='failed', ai_error=? WHERE id=?", (str(result)[:500], video_id))
            else:
                create_proposal(settings, video_id, result, notify=False)
                ready += 1
        db.set_setting("archive_batch", None)
        _notify(settings, f"{ready} vidéos d'archive prêtes à valider.\n{settings.public_url}/#archives")
        return

    if db.get_setting("archive_day") == date.today().isoformat():
        return
    with db.connect() as c:
        waiting = c.execute("SELECT COUNT(*) FROM proposals WHERE kind='archive' AND status IN ('pending','approved')"
                            ).fetchone()[0]
    wanted = settings.archive_per_day - waiting
    if wanted <= 0:
        return
    setup_experiment()
    items = []
    for video_id in archive_candidates(wanted * 2):
        if len(items) >= wanted:
            break
        video = _video(video_id)
        try:
            if video["caption_status"] != "fetched":
                if settings.has_youtube and not youtube.can_spend(settings, 250, archive=True):
                    break
                if not fetch_captions(settings, video_id, archive=True):
                    with db.connect() as c:
                        c.execute("UPDATE videos SET caption_status='none' WHERE id=?", (video_id,))
                    continue
            items.append((_video(video_id), ingest.video_passages(video_id)))
        except youtube.QuotaExceeded:
            break
        except Exception as exc:
            db.log(f"Sous-titres indisponibles pour {video_id} : {exc}", "warning")
    if items:
        db.set_setting("archive_batch", ai.submit_batch(settings, items))
        with db.connect() as c:
            c.executemany("UPDATE videos SET ai_status='queued' WHERE id=?", [(v["id"],) for v, _ in items])
        db.log(f"{len(items)} vidéos d'archive envoyées en traitement groupé")
    db.set_setting("archive_day", date.today().isoformat())


# --- Validation --------------------------------------------------------------------------------------------------

def approve(proposal_id: int, chosen: dict) -> None:
    with db.connect() as c:
        c.execute("UPDATE proposals SET status='approved', chosen=?, decided_at=? WHERE id=? AND status='pending'",
                  (json.dumps(chosen, ensure_ascii=False), db.now(), proposal_id))


def reject(proposal_id: int) -> None:
    with db.connect() as c:
        c.execute("UPDATE proposals SET status='rejected', decided_at=? WHERE id=? AND status='pending'",
                  (db.now(), proposal_id))


def regenerate(proposal_id: int, note: str = "") -> None:
    with db.connect() as c:
        row = c.execute("SELECT video_id FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        c.execute("UPDATE proposals SET status='rejected', decided_at=? WHERE id=?", (db.now(), proposal_id))
        c.execute("UPDATE videos SET ai_status='regen' WHERE id=?", (row["video_id"],))
    db.set_setting(f"regen_note:{row['video_id']}", note)


def default_choice(payload: dict, title_index: int = 0) -> dict:
    return {"title": payload["titles"][min(title_index, len(payload["titles"]) - 1)],
            "description": payload["description"], "tags": payload["tags"], "chapters": payload["chapters"],
            "thumbnail": False, "thumbnail_text": payload["thumbnail_text"]}


def handle_telegram(settings: Settings, bot: telegram.Bot, update: dict) -> None:
    query = update.get("callback_query")
    if not query:
        return
    # Seul le propriétaire du bot peut valider.
    if str(query["from"]["id"]) != str(settings.telegram_chat_id):
        bot.answer(query["id"], "Non autorisé")
        return
    action, proposal_id, *rest = query["data"].split(":")
    proposal_id = int(proposal_id)
    with db.connect() as c:
        row = c.execute("SELECT payload, status FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if not row or row["status"] != "pending":
        bot.answer(query["id"], "Déjà traité")
    elif action == "ok":
        approve(proposal_id, default_choice(json.loads(row["payload"]), int(rest[0])))
        bot.answer(query["id"], "Validé, publication en cours")
    elif action == "no":
        reject(proposal_id)
        bot.answer(query["id"], "Rejeté")
    elif action == "re":
        regenerate(proposal_id)
        bot.answer(query["id"], "Je refais une proposition")
    message = query.get("message")
    if message:
        bot.clear_buttons(message["chat"]["id"], message["message_id"])


# --- Publication -------------------------------------------------------------------------------------------------

def _footer(settings: Settings, video_id: str) -> str:
    return f"Texte et recherche dans tous les cours : {settings.public_url}/cours/{video_id}"


def publish(settings: Settings, proposal: dict, api: youtube.YouTube, only: set[str] | None = None) -> bool:
    """Publie étape par étape ; une étape faite n'est jamais refaite, même après un arrêt pour quota."""
    video_id, archive = proposal["video_id"], proposal["kind"] == "archive"
    payload, steps = json.loads(proposal["payload"]), json.loads(proposal["steps"] or "{}")
    chosen = json.loads(proposal["chosen"] or "{}") or default_choice(payload)
    video = _video(video_id)

    def done(step: str) -> None:
        steps[step] = db.now()
        with db.connect() as c:
            c.execute("UPDATE proposals SET steps=? WHERE id=?", (json.dumps(steps), proposal["id"]))

    def wanted(step: str) -> bool:
        return step not in steps and (only is None or step in only)

    with db.connect() as c:
        c.execute("INSERT OR IGNORE INTO backups VALUES (?,?,?,?,?)", (
            video_id, video["title"], video["description"], json.dumps(video["tags"], ensure_ascii=False), db.now()))

    if wanted("metadata"):
        # La description d'origine (livres, dons, site officiel) reste toujours en bas de la nouvelle.
        with db.connect() as c:
            original = c.execute("SELECT description FROM backups WHERE video_id=?", (video_id,)).fetchone()["description"]
        footer = "\n\n".join(x for x in (_footer(settings, video_id), original) if x)
        description = ai.compose_description(chosen["description"], chosen["chapters"], payload["hashtags"], footer)
        localizations = {lang: {"title": t["title"][:100], "description": t["description"][:4900]}
                         for lang, t in payload["translations"].items()}
        api.update_metadata(video_id, chosen["title"], description, chosen["tags"], localizations, archive)
        with db.connect() as c:
            c.execute("UPDATE videos SET title=?, description=?, tags=? WHERE id=?",
                      (chosen["title"], description, json.dumps(chosen["tags"], ensure_ascii=False), video_id))
        done("metadata")
    if wanted("captions"):
        api.upsert_caption(video_id, CAPTION_TRACK, captions.to_srt(ingest.video_passages(video_id)), archive)
        done("captions")
    if wanted("thumbnail") and chosen.get("thumbnail"):
        api.set_thumbnail(video_id, thumbnails.make(video_id, chosen["thumbnail_text"]), archive)
        done("thumbnail")

    complete = all(s in steps for s in ("metadata", "captions")) and (not chosen.get("thumbnail") or "thumbnail" in steps)
    if complete and proposal["status"] == "approved":
        with db.connect() as c:
            c.execute("UPDATE proposals SET status='published', published_at=?, error='' WHERE id=?",
                      (db.now(), proposal["id"]))
            c.execute("INSERT INTO posts(video_id, kind, text, start) VALUES (?,?,?,?)",
                      (video_id, "pensee", payload["pensee"]["text"], payload["pensee"]["start"]))
        db.log(f"Publié : {chosen['title']}")
        _notify(settings, f"Publié : <b>{chosen['title']}</b>\nhttps://youtu.be/{video_id}")
    return complete


def run_publish(settings: Settings) -> None:
    if not settings.has_youtube:
        return
    if shabbat.is_blocked(settings):
        db.log("Chabbat ou Yom Tov : publications en attente")
        return
    api = youtube.YouTube(settings)
    with db.connect() as c:
        approved = [dict(r) for r in c.execute("SELECT * FROM proposals WHERE status='approved' "
                                               "ORDER BY kind='archive', decided_at")]
        low_risk = [dict(r) for r in c.execute("SELECT * FROM proposals WHERE status='pending' ORDER BY id")]
    for proposal in approved:
        try:
            publish(settings, proposal, api)
        except youtube.QuotaExceeded as exc:
            db.log(f"{exc} : la suite reprend au prochain quota", "warning")
            return
        except Exception as exc:
            with db.connect() as c:
                c.execute("UPDATE proposals SET error=? WHERE id=?", (str(exc)[:500], proposal["id"]))
            db.log(f"Échec de publication : {exc}", "error")
    if db.get_setting("auto_low_risk", False):
        for proposal in low_risk:
            try:
                # Sous-titres corrigés : sans risque pour le titre ou l'image, donc sans validation si activé.
                publish(settings, proposal, api, only={"captions"})
            except youtube.QuotaExceeded:
                return
            except Exception as exc:
                db.log(f"Échec sous-titres : {exc}", "error")


def restore(settings: Settings, video_id: str) -> None:
    """Remet le titre, la description et les tags d'origine."""
    with db.connect() as c:
        backup = c.execute("SELECT * FROM backups WHERE video_id=?", (video_id,)).fetchone()
    if not backup:
        raise ValueError("Aucune sauvegarde pour cette vidéo")
    tags = json.loads(backup["tags"])
    if settings.has_youtube:
        youtube.YouTube(settings).update_metadata(video_id, backup["title"], backup["description"], tags)
    with db.connect() as c:
        c.execute("UPDATE videos SET title=?, description=?, tags=? WHERE id=?",
                  (backup["title"], backup["description"], backup["tags"], video_id))
    db.log(f"Vidéo restaurée : {backup['title']}")


# --- Diffusion ---------------------------------------------------------------------------------------------------

def run_diffusion(settings: Settings) -> None:
    """Une pensée par jour sur le canal Telegram, à partir de 8 h, jamais pendant Chabbat."""
    if not (settings.telegram_bot_token and settings.telegram_channel_id):
        return
    local = datetime.now(ZoneInfo(settings.timezone))
    if local.hour < 8 or db.get_setting("pensee_day") == local.date().isoformat() or shabbat.is_blocked(settings):
        return
    with db.connect() as c:
        post = c.execute("SELECT * FROM posts WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
    if not post:
        return
    url = search.video_url(post["video_id"], post["start"])
    telegram.Bot(settings).send(f"{post['text']}\n\n{url}", chat_id=settings.telegram_channel_id)
    with db.connect() as c:
        c.execute("UPDATE posts SET status='sent', sent_at=? WHERE id=?", (db.now(), post["id"]))
    db.set_setting("pensee_day", local.date().isoformat())


def newsletter(days: int = 7) -> str:
    """Brouillon de la newsletter de la semaine, à partir des articles publiés."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.connect() as c:
        rows = c.execute("SELECT * FROM proposals WHERE status='published' AND published_at>=? ORDER BY published_at DESC",
                         (since,)).fetchall()
    blocks = []
    for row in rows:
        payload = json.loads(row["payload"])
        title = (json.loads(row["chosen"] or "{}").get("title")) or payload["titles"][0]
        blocks.append(f"{title}\n{payload['pensee']['text']}\nhttps://youtu.be/{row['video_id']}")
    return "\n\n".join(blocks)


# --- Statistiques ------------------------------------------------------------------------------------------------

def run_analytics(settings: Settings) -> None:
    if not settings.has_youtube or db.get_setting("analytics_day") == date.today().isoformat():
        return
    if date.today().weekday() != 6:
        return
    api = youtube.YouTube(settings)
    with db.connect() as c:
        ids = [r["video_id"] for r in c.execute("SELECT video_id FROM experiment")]
    for video in api.details(ids) if ids else []:
        with db.connect() as c:
            c.execute("UPDATE experiment SET views_last=?, measured_at=? WHERE video_id=?",
                      (video["views"], db.now(), video["id"]))
            c.execute("UPDATE videos SET views=? WHERE id=?", (video["views"], video["id"]))
    db.set_setting("top_videos", api.top_videos())
    db.set_setting("analytics_day", date.today().isoformat())


def experiment_report() -> dict:
    with db.connect() as c:
        rows = c.execute("SELECT grp, COUNT(*) AS n, SUM(views_start) AS start, SUM(views_last) AS last "
                         "FROM experiment GROUP BY grp").fetchall()
    groups = {r["grp"]: {"videos": r["n"], "views_start": r["start"], "views_last": r["last"],
                         "gain": r["last"] - r["start"]} for r in rows}
    return {"started_at": db.get_setting("experiment_started_at"), "days": EXPERIMENT_DAYS, "groups": groups}


# --- Boucle ------------------------------------------------------------------------------------------------------

STEPS = (discover, check_captions, run_ai, run_archive, run_publish, run_diffusion, run_analytics)


def tick(settings: Settings) -> None:
    for step in STEPS:
        try:
            step(settings)
        except Exception as exc:
            db.log(f"{step.__name__} : {exc}", "error")
            traceback.print_exc()
    try:
        if embeddings.available():
            embeddings.index_missing(str(settings.model_dir))
    except Exception as exc:
        db.log(f"Index de recherche : {exc}", "error")
    db.set_setting("last_tick", db.now())


def run_worker(settings: Settings, stop: threading.Event) -> None:
    bot = telegram.Bot(settings) if settings.has_telegram else None
    last = 0.0
    while not stop.is_set():
        if datetime.now().timestamp() - last >= settings.poll_minutes * 60:
            tick(settings)
            last = datetime.now().timestamp()
        if bot:
            try:
                for update in bot.updates():
                    handle_telegram(settings, bot, update)
            except Exception as exc:
                db.log(f"Telegram : {exc}", "warning")
                stop.wait(30)
        else:
            stop.wait(30)
