"""Application web : recherche publique et tableau de bord de validation."""
import hashlib
import hmac
import html
import json
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import ai, captions, config, db, embeddings, ingest, pipeline, search, shabbat, shorts, thumbnails, youtube

settings = config.load()
db.restore_seed(settings.db_path, config.ROOT / "seed" / "studio-seed.db.gz")
db.init(settings.db_path)
WEB = Path(__file__).resolve().parent.parent / "web"
MODELS = str(settings.model_dir)
COOKIE = "studio_session"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = threading.Event()
    if db.get_setting("worker_enabled", True):
        threading.Thread(target=pipeline.run_worker, args=(settings, stop), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Nahman Studio", lifespan=lifespan)
app.mount("/web", StaticFiles(directory=WEB), name="web")


def _session_value() -> str:
    return hmac.new(settings.admin_password.encode(), b"studio-admin", hashlib.sha256).hexdigest()


def is_admin(request: Request) -> bool:
    return bool(settings.admin_password) and hmac.compare_digest(request.cookies.get(COOKIE, ""), _session_value())


def admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "Connexion requise")


@app.get("/")
def home():
    return FileResponse(WEB / "index.html")


@app.post("/api/login")
def login(response: Response, password: str = Body(embed=True)):
    if not settings.admin_password or not hmac.compare_digest(password, settings.admin_password):
        raise HTTPException(401, "Mot de passe incorrect")
    response.set_cookie(COOKIE, _session_value(), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 90,
                        secure=settings.public_url.startswith("https"))
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"admin": is_admin(request), "channel": settings.channel_name, "can_login": bool(settings.admin_password),
            "can_ask": settings.has_claude}


# --- Public ------------------------------------------------------------------------------------------------------

@app.get("/api/search")
def api_search(q: str):
    if not q.strip():
        return {"query": q, "results": [], "concepts": [], "semantic": False, "total_moments": 0}
    return search.search(q.strip()[:200], MODELS)


@app.post("/api/ask")
def api_ask(question: str = Body(embed=True)):
    question = question.strip()[:500]
    context = search.context_for_question(question, MODELS)
    extracts = [{k: c[k] for k in ("n", "title", "time", "url", "text")} for c in context]
    if not settings.has_claude:
        return {"mode": "extraits", "found": bool(context), "paragraphs": [], "sources": extracts}
    if not context:
        return {"mode": "ia", "found": False, "paragraphs": [], "sources": []}
    today = f"ask_count:{db.now()[:10]}"
    used = db.get_setting(today, 0)
    if used >= settings.ask_daily_limit:
        return {"mode": "extraits", "found": True, "paragraphs": [], "sources": extracts, "limit": True}
    db.set_setting(today, used + 1)
    result = ai.answer(settings, question, context)
    return {"mode": "ia", "found": result["found"], "paragraphs": result["paragraphs"], "sources": extracts}


@app.get("/api/sources")
def api_sources():
    return search.sources()


@app.get("/api/concepts")
def api_concepts():
    from . import glossary
    with db.connect() as c:
        counts = {r["concept"]: r["n"] for r in c.execute("SELECT concept, COUNT(DISTINCT video_id) n FROM concepts GROUP BY concept")}
    return [{"id": x["id"], "label": x["label"], "hebrew": x["hebrew"], "videos": counts.get(x["id"], 0)}
            for x in glossary.data()["concepts"]]


def _page(title: str, body: str, description: str = "") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description[:160])}"><link rel="stylesheet" href="/web/style.css"></head>
<body class="reader"><main class="article"><p><a href="/">← Rechercher dans tous les cours</a></p>{body}</main></body></html>""")


def _markdown(text: str) -> str:
    blocks = []
    for block in re.split(r"\n\s*\n", text.strip()):
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(block.strip()))
        heading = re.match(r"(#{2,3})\s+(.*)", safe)
        blocks.append(f"<h{len(heading[1])}>{heading[2]}</h{len(heading[1])}>" if heading else f"<p>{safe}</p>")
    return "\n".join(blocks)


@app.get("/cours/{video_id}")
def lesson(video_id: str):
    with db.connect() as c:
        video = c.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        proposal = c.execute("SELECT payload FROM proposals WHERE video_id=? AND status='published' ORDER BY id DESC",
                             (video_id,)).fetchone()
    if not video:
        raise HTTPException(404, "Cours introuvable")
    passages = ingest.video_passages(video_id)
    embed = (f'<div class="player"><iframe src="https://www.youtube-nocookie.com/embed/{html.escape(video_id)}" '
             f'title="Vidéo" allowfullscreen></iframe></div>')
    article = ""
    if proposal:
        data = json.loads(proposal["payload"])["article"]
        article = f"<h2>{html.escape(data['title'])}</h2>{_markdown(data['body'])}"
    lines = "".join(f'<p><a href="{search.video_url(video_id, p["start"])}">{captions.timestamp(p["start"])}</a> '
                    f'{html.escape(p["text"])}</p>' for p in passages)
    note = "<p class='muted'>Transcript automatique, il peut contenir des erreurs.</p>"
    return _page(video["title"], f"<h1>{html.escape(video['title'])}</h1>{embed}{article}<h2>Transcript</h2>{note}{lines}",
                 video["description"])


@app.get("/sitemap.xml")
def sitemap():
    with db.connect() as c:
        rows = c.execute("SELECT DISTINCT video_id FROM passages").fetchall()
    urls = "".join(f"<url><loc>{settings.public_url}/cours/{r['video_id']}</loc></url>" for r in rows)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                    media_type="application/xml")


# --- Tableau de bord ---------------------------------------------------------------------------------------------

def _proposal_view(row) -> dict:
    data = dict(row)
    data["payload"] = json.loads(data["payload"])
    data["chosen"] = json.loads(data["chosen"] or "{}")
    data["steps"] = json.loads(data["steps"] or "{}")
    data["url"] = f"https://youtu.be/{data['video_id']}"
    for item in data["payload"]["chapters"]:
        item["time"] = captions.timestamp(item["start"])
    for item in data["payload"]["shorts"]:
        item["time"] = captions.timestamp(item["start"])
        item["url"] = search.video_url(data["video_id"], item["start"])
    return data


@app.get("/api/status", dependencies=[Depends(admin)])
def status():
    with db.connect() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM proposals GROUP BY status")}
        kinds = {r["kind"]: r["n"] for r in c.execute("SELECT kind, COUNT(*) n FROM videos GROUP BY kind")}
        stats = c.execute("SELECT COUNT(DISTINCT video_id) videos, COUNT(*) passages FROM passages").fetchone()
        events = [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 12")]
        waiting = [dict(r) for r in c.execute("SELECT id, title, caption_checked_at FROM videos WHERE "
                                              "caption_status='pending' AND is_archive=0")]
    return {"keys": {"claude": settings.has_claude, "youtube": settings.has_youtube, "telegram": settings.has_telegram},
            "videos": kinds, "transcripts": stats["videos"], "passages": stats["passages"], "proposals": counts,
            "waiting_captions": waiting, "quota": {"spent": youtube.spent(), "limit": settings.youtube_daily_quota},
            "shabbat": shabbat.is_blocked(settings), "reopens_at": shabbat.next_opening(settings),
            "last_tick": db.get_setting("last_tick"), "events": events}


@app.get("/api/proposals", dependencies=[Depends(admin)])
def proposals(status: str = "pending", kind: str | None = None):
    query = ("SELECT p.*, v.title AS current_title, v.description AS current_description, v.tags AS current_tags, "
             "v.thumbnail, v.views, v.duration, v.published_at AS video_date FROM proposals p "
             "JOIN videos v ON v.id=p.video_id WHERE p.status=?")
    params = [status]
    if kind:
        query += " AND p.kind=?"
        params.append(kind)
    with db.connect() as c:
        return [_proposal_view(r) for r in c.execute(query + " ORDER BY p.kind='archive', p.id DESC LIMIT 60", params)]


@app.post("/api/proposals/{proposal_id}/approve", dependencies=[Depends(admin)])
def approve(proposal_id: int, chosen: dict = Body(embed=True)):
    pipeline.approve(proposal_id, chosen)
    return {"ok": True}


@app.post("/api/proposals/approve-all", dependencies=[Depends(admin)])
def approve_all(kind: str = Body(embed=True)):
    with db.connect() as c:
        rows = c.execute("SELECT id, payload FROM proposals WHERE status='pending' AND kind=?", (kind,)).fetchall()
    for row in rows:
        pipeline.approve(row["id"], pipeline.default_choice(json.loads(row["payload"])))
    return {"ok": True, "count": len(rows)}


@app.post("/api/proposals/{proposal_id}/reject", dependencies=[Depends(admin)])
def reject(proposal_id: int):
    pipeline.reject(proposal_id)
    return {"ok": True}


@app.post("/api/proposals/{proposal_id}/regenerate", dependencies=[Depends(admin)])
def regenerate(proposal_id: int, note: str = Body("", embed=True)):
    pipeline.regenerate(proposal_id, note)
    return {"ok": True}


@app.post("/api/videos/{video_id}/restore", dependencies=[Depends(admin)])
def restore(video_id: str):
    try:
        pipeline.restore(settings, video_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@app.get("/api/thumbnail/{video_id}", dependencies=[Depends(admin)])
def thumbnail(video_id: str, text: str):
    return Response(thumbnails.make(video_id, text[:60]), media_type="image/png")


@app.post("/api/shorts", dependencies=[Depends(admin)])
def make_short(video_id: str = Body(), start: int = Body(), end: int = Body(), title: str = Body(), hook: str = Body("")):
    out = settings.data_dir / "shorts"
    try:
        path = shorts.render(video_id, start, end, hook or title, out)
    except Exception as exc:
        raise HTTPException(500, f"Fabrication impossible : {str(exc)[:300]}")
    return {"ok": True, "file": f"/api/shorts/file/{path.parent.name}"}


@app.get("/api/shorts/file/{name}", dependencies=[Depends(admin)])
def short_file(name: str):
    path = (settings.data_dir / "shorts" / Path(name).name / "short.mp4")
    if not path.exists():
        raise HTTPException(404, "Short introuvable")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/archives", dependencies=[Depends(admin)])
def archives():
    with db.connect() as c:
        rows = c.execute("SELECT ai_status, COUNT(*) n FROM videos WHERE is_archive=1 AND kind!='short' GROUP BY ai_status").fetchall()
        next_up = [dict(r) for r in c.execute("SELECT id, title, views, duration, caption_status FROM videos WHERE "
                                              "is_archive=1 AND kind!='short' AND ai_status='todo' ORDER BY views DESC LIMIT 20")]
    total = sum(r["n"] for r in rows)
    done = sum(r["n"] for r in rows if r["ai_status"] == "done")
    return {"total": total, "done": done, "per_day": settings.archive_per_day, "enabled": db.get_setting("archive_enabled", False),
            "next": next_up, "experiment": pipeline.experiment_report()}


@app.get("/api/diffusion", dependencies=[Depends(admin)])
def diffusion():
    with db.connect() as c:
        posts = [dict(r) for r in c.execute("SELECT * FROM posts ORDER BY status='sent', id DESC LIMIT 30")]
    for post in posts:
        post["url"] = search.video_url(post["video_id"], post["start"])
    return {"posts": posts, "newsletter": pipeline.newsletter()}


@app.get("/api/insights", dependencies=[Depends(admin)])
def insights():
    return {"top": search.top_queries(), "missing": search.missing_topics(), "top_videos": db.get_setting("top_videos", [])[:15]}


@app.get("/api/settings", dependencies=[Depends(admin)])
def get_settings():
    with db.connect() as c:
        validated = c.execute("SELECT COUNT(*) FROM proposals WHERE status IN ('approved','published')").fetchone()[0]
    return {"archive_enabled": db.get_setting("archive_enabled", False), "auto_low_risk": db.get_setting("auto_low_risk", False),
            "validated": validated}


@app.post("/api/settings", dependencies=[Depends(admin)])
def post_settings(values: dict = Body(embed=True)):
    for key in ("archive_enabled", "auto_low_risk"):
        if key in values:
            db.set_setting(key, bool(values[key]))
    return {"ok": True}


@app.post("/api/tick", dependencies=[Depends(admin)])
def force_tick():
    threading.Thread(target=pipeline.tick, args=(settings,), daemon=True).start()
    return {"ok": True}


@app.get("/robots.txt")
def robots():
    return PlainTextResponse(f"User-agent: *\nDisallow: /api/\nSitemap: {settings.public_url}/sitemap.xml\n")
