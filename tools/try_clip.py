"""Fabrique un vrai extrait sans rien publier : python tools/try_clip.py VIDEO DEBUT FIN vertical|horizontal "Titre" """
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import clips, config, db  # noqa: E402

settings = config.load()
db.init(settings.db_path)
video_id, start, end, shape, title = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]
began = time.time()
path = clips.render(settings, {"id": f"essai-{shape}", "video_id": video_id, "start": start, "end": end, "format": shape,
                               "title": title, "hook": title})
print(path, round(path.stat().st_size / 1e6, 1), "Mo en", round(time.time() - began), "s")
