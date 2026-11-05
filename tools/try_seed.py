"""Vérifie qu'un premier démarrage sur un dossier vide repart bien des données embarquées."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["MODEL_DIR"] = str(Path(__file__).resolve().parent.parent / "data" / "models")
os.environ["ADMIN_PASSWORD"] = "essai"

from fastapi.testclient import TestClient  # noqa: E402

from studio import app  # noqa: E402

client = TestClient(app.app)
print("me", client.get("/api/me").status_code)
found = client.get("/api/search", params={"q": "tikoun aklali"}).json()
print("recherche", found["total_moments"], "moments, par le sens :", found["semantic"])
print("connexion", client.post("/api/login", json={"password": "essai"}).status_code)
print("propositions", len(client.get("/api/proposals", params={"status": "pending", "kind": "live"}).json()))
print("sources", sum(len(b["refs"]) for b in client.get("/api/sources").json()), "références")
status = client.get("/api/status").json()
print("transcripts", status["transcripts"], "passages", status["passages"], "videos", status["videos"])
