import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio import config, db  # noqa: E402


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    loaded = config.load()
    db.init(loaded.db_path)
    return dataclasses.replace(loaded, public_url="https://exemple.test")


PAYLOAD = {
    "corrections": [{"wrong": "tique un", "right": "Tikoun"}],
    "titles": ["Le Tikoun Haklali expliqué", "Pourquoi réciter les dix psaumes", "La force du Tikoun Haklali"],
    "description": "Un cours sur le Tikoun Haklali.",
    "chapters": [{"start": 30, "title": "Le sujet"}, {"start": 35, "title": "Trop proche"}, {"start": 300, "title": "Suite"},
                 {"start": 99999, "title": "Hors vidéo"}],
    "tags": ["tikoun haklali", "breslev", "rabbi nahman"],
    "hashtags": ["#breslev"],
    "thumbnail_text": "Le Tikoun Haklali",
    "shorts": [{"start": 60, "end": 120, "title": "Dix psaumes", "hook": "Une promesse"},
               {"start": 500, "end": 400, "title": "Invalide", "hook": ""}],
    "article": {"title": "Le Tikoun Haklali", "body": "## Sujet\n\nLe Rav explique."},
    "pensee": {"text": "Il est interdit de désespérer.", "start": 60},
    "sources": [{"start": 60, "book": "Likoutey Moharan", "ref": "II, 92", "label": "Tikoun Haklali"}],
    "concepts": [{"start": 60, "concept": "tikoun-haklali"}],
    "translations": {"en": {"title": "The General Remedy", "description": "A lesson."},
                     "he": {"title": "התיקון הכללי", "description": "שיעור."},
                     "es": {"title": "El remedio general", "description": "Una clase."}},
}
