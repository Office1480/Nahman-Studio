"""Essai rapide du moteur de recherche sur la base locale : python tools/try_search.py "tikoun aklali" ..."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import config, db, search  # noqa: E402

settings = config.load()
db.init(settings.db_path)
for query in sys.argv[1:]:
    found = search.search(query, str(settings.data_dir / "models"), log=False)
    print(f"--- {query} : {found['total_moments']} moments, {len(found['results'])} vidéos, "
          f"concepts {[c['label'] for c in found['concepts']]}")
    for result in found["results"][:3]:
        moment = result["moments"][0]
        origin = "SENS" if moment["by_meaning"] else "MOTS"
        print(f"   {result['title'][:48]} | {moment['time']} {origin} | {re.sub('<[^>]+>', '', moment['html'])[:115]}")
