"""Charge dans la base locale la proposition de démonstration, comme si Claude venait de la produire."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio import config, db, pipeline  # noqa: E402

settings = config.load()
db.init(settings.db_path)
payload = json.loads((ROOT / "tools" / "demo_proposal.json").read_text(encoding="utf-8"))
with db.connect() as c:
    c.execute("UPDATE videos SET is_archive=0 WHERE id='tEenadgsY9k'")
print("proposition", pipeline.create_proposal(settings, "tEenadgsY9k", payload))
