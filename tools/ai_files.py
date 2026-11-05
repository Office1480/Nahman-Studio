"""Traitement IA sans clé API : prépare les fichiers, vérifie les résultats, les importe dans le circuit normal.

python tools/ai_files.py export          écrit consignes, schéma et transcripts dans data/ai
python tools/ai_files.py check VIDEO     vérifie un résultat data/ai/VIDEO.json
python tools/ai_files.py import          crée les propositions à partir des résultats valides
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio import ai, clips, config, db, ingest, pipeline  # noqa: E402

settings = config.load()
db.init(settings.db_path)
FOLDER = settings.data_dir / "ai"


def pending_videos() -> list[str]:
    with db.connect() as c:
        return [r["id"] for r in c.execute("SELECT id FROM videos WHERE is_archive=0 AND caption_status='fetched' "
                                           "AND kind!='short' AND ai_status='todo'")]


def export() -> None:
    FOLDER.mkdir(exist_ok=True)
    (FOLDER / "system.txt").write_text(ai.system_prompt(settings), encoding="utf-8")
    (FOLDER / "schema.json").write_text(json.dumps(ai.VIDEO_SCHEMA, ensure_ascii=False, indent=1), encoding="utf-8")
    for video_id in pending_videos():
        video = pipeline._video(video_id)
        (FOLDER / f"{video_id}.txt").write_text(ai._user_message(video, ingest.video_passages(video_id)), encoding="utf-8")
        print(video_id, video["title"])


def validate(value, schema, path="racine") -> list[str]:
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path} doit être un objet"]
        errors = [f"{path}.{k} manque" for k in schema["required"] if k not in value]
        errors += [f"{path}.{k} n'est pas prévu" for k in value if k not in schema["properties"]]
        for key, sub in schema["properties"].items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
        return errors
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path} doit être une liste"]
        return [e for i, item in enumerate(value) for e in validate(item, schema["items"], f"{path}[{i}]")]
    expected = {"string": str, "integer": int}[kind]
    return [] if isinstance(value, expected) else [f"{path} doit être de type {kind}"]


def check(video_id: str) -> list[str]:
    path = FOLDER / f"{video_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"lecture impossible : {exc}"]
    errors = validate(payload, ai.VIDEO_SCHEMA)
    if errors:
        return errors
    video = pipeline._video(video_id)
    transcript = " ".join(p["text"] for p in ingest.video_passages(video_id))
    errors += [f"correction introuvable dans le transcript : {c['wrong']}" for c in payload["corrections"]
               if c["wrong"] not in transcript]
    if len(payload["titles"]) != 3:
        errors.append("il faut 3 titres")
    errors += [f"titre trop long : {t}" for t in payload["titles"] if len(t) > 100]
    if not 6 <= len(payload["chapters"]) <= 15:
        errors.append("il faut 6 à 15 chapitres")
    if not payload["chapters"] or payload["chapters"][0]["start"] != 0:
        errors.append("le premier chapitre doit commencer à 0")
    errors += [f"horodatage hors de la vidéo : {c['start']}" for c in payload["chapters"] if c["start"] >= video["duration"]]
    errors += [f"Short de {s['end'] - s['start']} s : {s['title']}" for s in payload["shorts"]
               if not 30 <= s["end"] - s["start"] <= 90 or s["end"] > video["duration"]]
    words = len(payload["article"]["body"].split())
    if not 500 <= words <= 1100:
        errors.append(f"article de {words} mots, il en faut 600 à 1000")
    if len(payload["pensee"]["text"]) > 280:
        errors.append("pensée trop longue")
    return errors


def import_all() -> None:
    for video_id in pending_videos():
        if not (FOLDER / f"{video_id}.json").exists():
            print("absent", video_id)
            continue
        errors = check(video_id)
        if errors:
            print("refusé", video_id, errors[:3])
            continue
        payload = json.loads((FOLDER / f"{video_id}.json").read_text(encoding="utf-8"))
        print("proposition", pipeline.create_proposal(settings, video_id, payload), video_id)


CLIPS = FOLDER / "clips"


def clip_videos() -> list[str]:
    with db.connect() as c:
        return [r["id"] for r in c.execute("SELECT id FROM videos WHERE is_archive=0 AND caption_status='fetched' "
                                           "AND kind!='short' AND clips_status='todo'")]


def export_clips() -> None:
    CLIPS.mkdir(parents=True, exist_ok=True)
    (CLIPS / "system.txt").write_text(ai.clip_prompt(settings), encoding="utf-8")
    (CLIPS / "schema.json").write_text(json.dumps(ai.CLIP_SCHEMA, ensure_ascii=False, indent=1), encoding="utf-8")
    for video_id in clip_videos():
        video = pipeline._video(video_id)
        (CLIPS / f"{video_id}.txt").write_text(ai.clip_message(video, ingest.video_cues(video_id)), encoding="utf-8")
        print(video_id, video["title"])


def check_clips(video_id: str) -> list[str]:
    try:
        payload = json.loads((CLIPS / f"{video_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"lecture impossible : {exc}"]
    errors = validate(payload, ai.CLIP_SCHEMA)
    if errors:
        return errors
    duration = pipeline._video(video_id)["duration"]
    for key, low, high in (("stories", 25, 1800), ("moments", 45, 600), ("shorts", 15, 120)):
        for item in payload[key]:
            length = item["end"] - item["start"]
            if not low <= length <= high or item["end"] > duration:
                errors.append(f"{key} de {length} s à {item['start']} : {item['title']}")
            if len(item["title"]) > 100:
                errors.append(f"titre trop long : {item['title']}")
    if not 2 <= len(payload["moments"]) <= 6:
        errors.append("il faut 2 à 6 moments")
    if not 3 <= len(payload["shorts"]) <= 8:
        errors.append("il faut 3 à 8 Shorts")
    return errors


def import_clips() -> None:
    with db.connect() as c:
        rows = c.execute("SELECT p.video_id, p.payload, v.duration FROM proposals p JOIN videos v ON v.id=p.video_id "
                         "WHERE p.status='pending'").fetchall()
    for row in rows:
        clips.add(row["video_id"], "short", json.loads(row["payload"])["shorts"], row["duration"])
    for video_id in clip_videos():
        errors = check_clips(video_id)
        if errors:
            print("refusé", video_id, errors[:3])
            continue
        payload = json.loads((CLIPS / f"{video_id}.json").read_text(encoding="utf-8"))
        print(clips.store_detection(video_id, payload, pipeline._video(video_id)["duration"]), "extraits", video_id)


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "export":
        export()
    elif command == "import":
        import_all()
    elif command == "export-clips":
        export_clips()
    elif command == "import-clips":
        import_clips()
    elif command == "check-clips":
        problems = check_clips(sys.argv[2])
        print("\n".join(problems) if problems else "OK")
        sys.exit(1 if problems else 0)
    else:
        problems = check(sys.argv[2])
        print("\n".join(problems) if problems else "OK")
        sys.exit(1 if problems else 0)
