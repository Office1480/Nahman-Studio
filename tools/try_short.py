"""Fabrique un vrai Short depuis la base locale : python tools/try_short.py VIDEO DEBUT FIN "Accroche" """
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import config, db, shorts  # noqa: E402

settings = config.load()
db.init(settings.db_path)
video_id, start, end, hook = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
began = time.time()
path = shorts.render(video_id, start, end, hook, settings.data_dir / "shorts")
print(path, round(path.stat().st_size / 1e6, 1), "Mo en", round(time.time() - began), "s")
