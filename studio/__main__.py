"""Commandes : python -m studio serve | import-channel | import-subs | auth-youtube | tick | reindex"""
import argparse
import http.server
import time
import urllib.parse
import webbrowser

from . import agent, config, db, embeddings, ingest, pipeline, youtube, ytdlp_source


def import_channel(settings) -> None:
    videos = ytdlp_source.inventory(settings.channel_id)
    for video in videos:
        ingest.upsert_video(video, is_archive=True)
    print(f"{len(videos)} vidéos dans l'inventaire")


def import_subs(settings, limit: int, pause: float) -> None:
    """Récupère les sous-titres des archives, les plus vues d'abord, sans consommer de quota."""
    with db.connect() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM videos WHERE caption_status='pending' AND is_archive=1 "
                                          "ORDER BY views DESC LIMIT ?", (limit,))]
    for position, video_id in enumerate(ids, 1):
        try:
            info = ytdlp_source.fetch(video_id)
        except Exception as exc:
            print(f"{video_id} : erreur, pause de 60 s ({str(exc)[-100:]})")
            time.sleep(60)
            continue
        ingest.upsert_video({"id": video_id, **{k: info[k] for k in ("description", "tags", "published_at", "views",
                                                                     "duration")}})
        if info["vtt"]:
            ingest.store_transcript(video_id, info["vtt"], "yt-dlp")
        else:
            with db.connect() as c:
                c.execute("UPDATE videos SET caption_status='none' WHERE id=?", (video_id,))
        print(f"{position}/{len(ids)} {video_id}", flush=True)
        time.sleep(pause)


def latest_lives(settings, count: int) -> None:
    """Fait passer les derniers lives dans le circuit normal : sous-titres, puis proposition."""
    lives = ytdlp_source.latest_lives(settings.channel_id, count)
    for position, live in enumerate(lives, 1):
        ingest.upsert_video(live)
        with db.connect() as c:
            c.execute("UPDATE videos SET is_archive=0 WHERE id=?", (live["id"],))
            status = c.execute("SELECT caption_status FROM videos WHERE id=?", (live["id"],)).fetchone()[0]
        available = status == "fetched" or pipeline.fetch_captions(settings, live["id"])
        with db.connect() as c:
            title = c.execute("SELECT title FROM videos WHERE id=?", (live["id"],)).fetchone()[0]
            passages = c.execute("SELECT COUNT(*) FROM passages WHERE video_id=?", (live["id"],)).fetchone()[0]
        print(f"{position}/{len(lives)} {live['id']} {passages} passages {'' if available else 'SANS SOUS-TITRES '}{title}",
              flush=True)
        time.sleep(3)
    pipeline.run_ai(settings)


def auth_youtube(settings) -> None:
    """Autorise l'application sur la chaîne et affiche le jeton à mettre dans .env."""
    port = 8765
    redirect = f"http://localhost:{port}"
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            received.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Autorisation reçue, tu peux fermer cette page.".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", port), Handler)
    url = youtube.authorize_url(settings, redirect)
    print(f"Ouvre cette page si le navigateur ne s'ouvre pas :\n{url}")
    webbrowser.open(url)
    while "code" not in received and "error" not in received:
        server.handle_request()
    if "error" in received:
        raise SystemExit(f"Autorisation refusée : {received['error'][0]}")
    token = youtube.exchange_code(settings, received["code"][0], redirect)
    path = config.ROOT / ".env"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing.rstrip() + f"\nGOOGLE_REFRESH_TOKEN={token}\n", encoding="utf-8")
    print("Jeton enregistré dans .env")


def main() -> None:
    parser = argparse.ArgumentParser(prog="studio")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--host", default="127.0.0.1")
    commands.add_parser("import-channel")
    subs = commands.add_parser("import-subs")
    subs.add_argument("--limit", type=int, default=2000)
    subs.add_argument("--pause", type=float, default=5)
    lives = commands.add_parser("latest-lives")
    lives.add_argument("--count", type=int, default=10)
    commands.add_parser("agent")
    commands.add_parser("auth-youtube")
    commands.add_parser("tick")
    commands.add_parser("reindex")
    args = parser.parse_args()

    settings = config.load()
    db.restore_seed(settings.db_path, config.ROOT / "seed" / "studio-seed.db.gz")
    db.init(settings.db_path)
    if args.command == "serve":
        import uvicorn
        uvicorn.run("studio.app:app", host=args.host, port=args.port)
    elif args.command == "import-channel":
        import_channel(settings)
    elif args.command == "import-subs":
        import_subs(settings, args.limit, args.pause)
    elif args.command == "agent":
        agent.run(settings)
    elif args.command == "latest-lives":
        latest_lives(settings, args.count)
    elif args.command == "auth-youtube":
        auth_youtube(settings)
    elif args.command == "tick":
        pipeline.tick(settings)
    elif args.command == "reindex":
        ingest.refresh_all()
        total = 0
        while count := embeddings.index_missing(str(settings.model_dir)):
            total += count
            print(total, flush=True)


if __name__ == "__main__":
    main()
