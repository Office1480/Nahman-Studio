"""Prépare les données embarquées dans l'image : inventaire, transcripts, index et propositions."""
import gzip
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio import config  # noqa: E402

settings = config.load()
target = ROOT / "seed" / "studio-seed.db.gz"
target.parent.mkdir(exist_ok=True)
copy = settings.data_dir / "seed-copy.db"
copy.unlink(missing_ok=True)

source = sqlite3.connect(settings.db_path)
destination = sqlite3.connect(copy)
source.backup(destination)
source.close()
destination.executescript("""
    DELETE FROM events;
    DELETE FROM search_log;
    DELETE FROM quota;
    DELETE FROM settings;
    PRAGMA journal_mode=DELETE;
    VACUUM;
""")
destination.close()

with open(copy, "rb") as raw, gzip.open(target, "wb", compresslevel=9) as packed:
    shutil.copyfileobj(raw, packed)
print(target, round(target.stat().st_size / 1e6, 1), "Mo, base de", round(copy.stat().st_size / 1e6, 1), "Mo")
copy.unlink()
