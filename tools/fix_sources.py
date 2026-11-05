"""Relecture des sources : unifie les noms de livres et signale les références déduites."""
import json
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent / "data" / "ai"
BOOKS = {"Alim LeTroufa": "Alim LiTroufa", "Alim LiTeroufa": "Alim LiTroufa", "Lettres de Rabbi Nathan": "Alim LiTroufa"}

for path in FOLDER.glob("*.json"):
    if path.name == "schema.json":
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    for source in data["sources"]:
        source["book"] = BOOKS.get(source["book"], source["book"])
        if source["book"] == "Hayey Moharan" and source["ref"] == "69":
            source["ref"] = "69 (livre à vérifier)"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
