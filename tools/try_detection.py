"""Répétition réelle de la détection, sur une base temporaire : la dernière vidéo de la chaîne joue la nouveauté."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
from studio import config, db, pipeline, shabbat  # noqa: E402

settings = config.load()
db.init(settings.db_path)

print("1er passage (archives, aucune notification) :", pipeline.discover(settings))
with db.connect() as c:
    print("vidéos dans le flux :", c.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
    newest = c.execute("SELECT id, title FROM videos WHERE id IN (SELECT id FROM videos ORDER BY published_at DESC LIMIT 1)").fetchone()
    c.execute("DELETE FROM videos WHERE id=?", (newest["id"],))

found = pipeline.discover(settings)
print("2e passage, nouvelle vidéo détectée :", found, newest["title"].encode("ascii", "replace").decode())
pipeline.check_captions(settings)
with db.connect() as c:
    row = c.execute("SELECT caption_status, kind, duration FROM videos WHERE id=?", (newest["id"],)).fetchone()
    count = c.execute("SELECT COUNT(*) FROM passages WHERE video_id=?", (newest["id"],)).fetchone()[0]
    raw = c.execute("SELECT LENGTH(raw) FROM transcripts WHERE video_id=?", (newest["id"],)).fetchone()
print("sous-titres :", dict(row), "passages :", count, "taille du transcript :", raw[0] if raw else 0)

windows = shabbat.fetch_windows(settings)
print("fenêtres Chabbat/fêtes sur 30 jours :", len(windows))
for start, end in sorted(windows)[:4]:
    print("  ", start.isoformat(), "->", end.isoformat())
print("publication bloquée maintenant :", shabbat.is_blocked(settings))
