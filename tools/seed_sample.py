"""Charge l'inventaire réel de la chaîne et les sous-titres des N vidéos longues les plus récentes."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import config, db, ingest, ytdlp_source  # noqa: E402

settings = config.load()
db.init(settings.db_path)
count = int(sys.argv[1]) if len(sys.argv) > 1 else 40

videos = []
for tab, kind in (("videos", "long"), ("streams", "live"), ("shorts", "short")):
    path = settings.data_dir / "import" / f"inventory_{tab}.json"
    for e in json.loads(path.read_text(encoding="utf-8"))["entries"]:
        videos.append({"id": e["id"], "title": e.get("title") or "", "kind": kind,
                       "duration": int(e.get("duration") or 0), "views": e.get("view_count") or 0,
                       "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg"})
for v in videos:
    ingest.upsert_video(v, is_archive=True)
print("inventaire:", len(videos), flush=True)

streams = [v["id"] for v in videos if v["kind"] == "live"][: count // 2]
longs = sorted((v for v in videos if v["kind"] == "long"), key=lambda v: -v["views"])
targets = streams + [v["id"] for v in longs[: count - len(streams)]]
done = 0
for vid in targets:
    try:
        info = ytdlp_source.fetch(vid)
    except Exception as exc:
        print("erreur", vid, str(exc)[-120:].encode("ascii", "replace").decode(), flush=True)
        time.sleep(20)
        continue
    ingest.upsert_video({"id": vid, **{k: info[k] for k in ("description", "tags", "published_at", "views", "duration")}})
    if info["vtt"]:
        n = ingest.store_transcript(vid, info["vtt"], "yt-dlp")
        done += 1
        print(done, vid, n, "passages", flush=True)
    else:
        print("pas de sous-titres", vid, flush=True)
    time.sleep(4)
print("fini:", done, flush=True)
