"""Programme du PC : extrait les vidéos validées sur le serveur, depuis une connexion domestique."""
import shutil
import time

import httpx

from . import clips
from .config import Settings

IDLE_SECONDS = 45
BOT_CHECK_PAUSE = 1800


def run_once(settings: Settings, http: httpx.Client, make=clips.render) -> str:
    """Traite au plus un extrait. Retourne ce qui s'est passé : rien, fait, refuse ou échec."""
    response = http.get("/api/agent/next")
    response.raise_for_status()
    clip = response.json()["clip"]
    if not clip:
        return "rien"
    print(f"Extraction : {clip['title']}", flush=True)
    try:
        path = make(settings, clip, clip["cues"])
    except clips.BotCheck as exc:
        http.post(f"/api/agent/{clip['id']}/fail", json={"error": str(exc), "retry": True})
        return "refus"
    except Exception as exc:
        http.post(f"/api/agent/{clip['id']}/fail", json={"error": str(exc), "retry": False})
        return "échec"
    with open(path, "rb") as handle:
        http.post(f"/api/agent/{clip['id']}/file", content=handle, timeout=1800).raise_for_status()
    shutil.rmtree(path.parent, ignore_errors=True)
    print(f"Envoyé : {clip['title']}", flush=True)
    return "fait"


def run(settings: Settings) -> None:
    if not (settings.agent_url and settings.agent_token):
        raise SystemExit("AGENT_URL et AGENT_TOKEN manquent dans .env")
    print(f"Connecté à {settings.agent_url}, en attente d'extraits validés.", flush=True)
    http = httpx.Client(base_url=settings.agent_url, headers={"X-Agent-Token": settings.agent_token}, timeout=60)
    while True:
        try:
            result = run_once(settings, http)
        except (httpx.HTTPError, OSError) as exc:
            print(f"Serveur injoignable : {exc}", flush=True)
            result = "rien"
        if result == "refus":
            print("Vérification anti-robots de YouTube : nouvel essai dans 30 minutes.", flush=True)
            time.sleep(BOT_CHECK_PAUSE)
        elif result == "rien":
            time.sleep(IDLE_SECONDS)
