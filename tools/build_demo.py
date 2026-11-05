"""Fabrique la page de démonstration autonome à partir de la base locale."""
import base64
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio import captions, config, db, glossary, pipeline, search, thumbnails  # noqa: E402

DEMO_VIDEO = "tEenadgsY9k"


def data_uri(image: bytes, width: int, quality: int = 62) -> str:
    picture = Image.open(io.BytesIO(image)).convert("RGB")
    picture.thumbnail((width, width))
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def main() -> None:
    settings = config.load()
    db.init(settings.db_path)
    payload = json.loads((ROOT / "tools" / "demo_proposal.json").read_text(encoding="utf-8"))
    with db.connect() as c:
        rows = c.execute("SELECT v.* FROM videos v WHERE v.id IN (SELECT DISTINCT video_id FROM passages)").fetchall()
        videos = {}
        for r in rows:
            image = httpx.get(f"https://i.ytimg.com/vi/{r['id']}/mqdefault.jpg", timeout=20).content
            videos[r["id"]] = {"title": r["title"], "published_at": r["published_at"], "kind": r["kind"],
                               "views": r["views"], "thumb": data_uri(image, 240)}
        passages = [{"v": p["video_id"], "s": p["start"], "e": p["end"], "t": p["text"], "phon": p["phon"]}
                    for p in c.execute("SELECT * FROM passages ORDER BY video_id, start")]
        kinds = {r["kind"]: r["n"] for r in c.execute("SELECT kind, COUNT(*) n FROM videos GROUP BY kind")}
        next_up = [dict(r) for r in c.execute("SELECT id, title, views, duration, caption_status FROM videos WHERE "
                                              "kind!='short' ORDER BY views DESC LIMIT 20")]
        video = dict(c.execute("SELECT * FROM videos WHERE id=?", (DEMO_VIDEO,)).fetchone())

    for item in payload["chapters"]:
        item["time"] = captions.timestamp(item["start"])
    for item in payload["shorts"]:
        item["time"] = captions.timestamp(item["start"])
        item["url"] = search.video_url(DEMO_VIDEO, item["start"])

    preview = thumbnails.make(DEMO_VIDEO, payload["thumbnail_text"])
    proposal = {"id": 1, "video_id": DEMO_VIDEO, "kind": "live", "status": "pending", "payload": payload, "chosen": {},
                "steps": {}, "url": f"https://youtu.be/{DEMO_VIDEO}", "current_title": video["title"],
                "thumbnail": videos[DEMO_VIDEO]["thumb"], "views": video["views"], "duration": video["duration"],
                "thumbnail_preview": data_uri(preview, 640, 72)}
    sources = {}
    for s in payload["sources"]:
        sources.setdefault(s["book"], []).append({"ref": s["ref"], "label": s["label"], "moments": [{
            "video_id": DEMO_VIDEO, "title": video["title"], "time": captions.timestamp(s["start"]),
            "url": search.video_url(DEMO_VIDEO, s["start"])}]})

    demo = {
        "channel": settings.channel_name,
        "banner": f"Démonstration : {len(videos)} vrais cours chargés sur {kinds.get('long', 0) + kinds.get('live', 0)}. "
                  "Rien n'est publié. La recherche par le sens et les réponses rédigées demandent l'application complète.",
        "videos": videos, "passages": passages, "concepts": glossary.data()["concepts"], "stop": sorted(search.STOP),
        "chapters": {DEMO_VIDEO: payload["chapters"]}, "proposals": [proposal],
        "sources": [{"book": book, "refs": refs} for book, refs in sources.items()],
        "archives": {"total": kinds.get("long", 0) + kinds.get("live", 0), "done": 0, "per_day": settings.archive_per_day,
                     "enabled": False, "next": next_up, "experiment": {"started_at": None, "days": 28, "groups": {}}},
        "diffusion": {"posts": [{"text": payload["pensee"]["text"], "status": "queued",
                                 "url": search.video_url(DEMO_VIDEO, payload["pensee"]["start"])}],
                      "newsletter": f"{payload['titles'][0]}\n{payload['pensee']['text']}\nhttps://youtu.be/{DEMO_VIDEO}"},
        "status": {"keys": {"claude": False, "youtube": False, "telegram": False}, "videos": kinds,
                   "transcripts": len(videos), "passages": len(passages), "proposals": {"pending": 1},
                   "waiting_captions": [], "quota": {"spent": 0, "limit": settings.youtube_daily_quota}, "shabbat": False,
                   "events": []},
    }

    def script(text: str) -> str:
        return text.replace("</script", "<\\/script")

    html = f"""<title>Nahman Studio</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Assistant:wght@400;600;700&family=Frank+Ruhl+Libre:wght@500;600&display=swap">
<style>{(ROOT / 'web' / 'style.css').read_text(encoding='utf-8')}</style>
<header class="top"><a class="brand" href="#recherche"><span class="brand-mark" aria-hidden="true">נ</span><span>Nahman Studio</span></a>
<nav id="nav" aria-label="Sections"></nav><button id="login-button" class="link" type="button" hidden>Connexion</button></header>
<div id="demo-banner" class="banner" hidden></div><main id="view" tabindex="-1"></main><div id="toast" role="status" aria-live="polite"></div>
<script>window.DEMO_DATA={script(json.dumps(demo, ensure_ascii=False))};</script>
<script>{script((ROOT / 'tools' / 'demo.js').read_text(encoding='utf-8'))}</script>
<script>{script((ROOT / 'web' / 'app.js').read_text(encoding='utf-8'))}</script>
"""
    out = settings.data_dir.parent / "dist" / "nahman-studio-demo.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(out, round(out.stat().st_size / 1e6, 2), "Mo", len(videos), "cours", len(passages), "passages")


if __name__ == "__main__":
    main()
