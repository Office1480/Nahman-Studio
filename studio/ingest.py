"""Enregistrement des vidéos et des transcripts."""
import json
import re

from . import captions, db, glossary


def upsert_video(v: dict, is_archive: bool = False) -> bool:
    """Ajoute ou met à jour une vidéo. Retourne True si elle est nouvelle."""
    with db.connect() as c:
        known = c.execute("SELECT 1 FROM videos WHERE id=?", (v["id"],)).fetchone()
        if known:
            updates = {k: v[k] for k in ("title", "description", "published_at", "duration", "views", "thumbnail",
                                         "live_ended", "kind") if v.get(k) not in (None, "")}
            if v.get("tags"):
                updates["tags"] = json.dumps(v["tags"], ensure_ascii=False)
            if updates:
                c.execute(f"UPDATE videos SET {', '.join(k + '=?' for k in updates)} WHERE id=?",
                          (*updates.values(), v["id"]))
            return False
        c.execute(
            "INSERT INTO videos(id, title, description, tags, published_at, duration, kind, live_ended, views,"
            " thumbnail, is_archive, created_at, ai_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (v["id"], v["title"], v.get("description", ""), json.dumps(v.get("tags", []), ensure_ascii=False),
             v.get("published_at"), v.get("duration", 0), v.get("kind", "long"), v.get("live_ended", 1),
             v.get("views", 0), v.get("thumbnail", ""), int(is_archive), db.now(),
             "skip" if v.get("kind") == "short" else "todo"))
    return True


def store_transcript(video_id: str, vtt: str, source: str) -> int:
    """Sauvegarde le transcript intégral et son découpage horodaté. Retourne le nombre de passages."""
    passages = captions.to_passages(captions.parse_vtt(vtt))
    with db.connect() as c:
        c.execute("INSERT INTO transcripts(video_id, raw, source, fetched_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(video_id) DO UPDATE SET raw=excluded.raw, source=excluded.source, "
                  "fetched_at=excluded.fetched_at", (video_id, vtt, source, db.now()))
        c.execute("DELETE FROM vectors WHERE passage_id IN (SELECT id FROM passages WHERE video_id=?)", (video_id,))
        c.execute("DELETE FROM passages WHERE video_id=?", (video_id,))
        for p in passages:
            text = glossary.fix_misheard(p["text"])
            c.execute("INSERT INTO passages(video_id, start, end, text, raw_text, phon) VALUES (?,?,?,?,?,?)",
                      (video_id, p["start"], p["end"], text, p["text"], glossary.phon(text)))
        c.execute("UPDATE videos SET caption_status='fetched', caption_checked_at=? WHERE id=?", (db.now(), video_id))
    return len(passages)


def apply_corrections(video_id: str, corrections: list[dict]) -> int:
    """Applique les corrections proposées par l'IA à tous les passages d'une vidéo."""
    changed = 0
    with db.connect() as c:
        for row in c.execute("SELECT id, text FROM passages WHERE video_id=?", (video_id,)).fetchall():
            text = row["text"]
            for fix in corrections:
                if fix["wrong"]:
                    text = re.sub(rf"(?<!\w){re.escape(fix['wrong'])}(?!\w)", lambda _: fix["right"], text)
            if text != row["text"]:
                c.execute("UPDATE passages SET text=?, phon=? WHERE id=?", (text, glossary.phon(text), row["id"]))
                changed += 1
    return changed


def refresh_all() -> None:
    """Réapplique le glossaire à tous les transcripts stockés, par exemple après l'avoir enrichi."""
    with db.connect() as c:
        rows = c.execute("SELECT video_id, raw, source FROM transcripts WHERE video_id NOT IN "
                         "(SELECT video_id FROM proposals)").fetchall()
    for row in rows:
        store_transcript(row["video_id"], row["raw"], row["source"])


def video_passages(video_id: str) -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT id, start, end, text FROM passages WHERE video_id=? ORDER BY start",
                                           (video_id,))]
