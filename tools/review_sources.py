"""Affiche les sources détectées dans les résultats de data/ai, pour relecture."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

for path in sorted((ROOT / "data" / "ai").glob("*.json")):
    if path.name == "schema.json":
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    print(path.stem, "|", data["titles"][0], "|", len(data["chapters"]), "chapitres |",
          len(data["article"]["body"].split()), "mots |", len(data["corrections"]), "corrections")
    for source in data["sources"]:
        print("   ", source["start"], source["book"], "|", source["ref"], "|", source["label"])
