import gzip
import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    published_at TEXT,
    duration INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'long',              -- long | live | short
    live_ended INTEGER DEFAULT 1,
    views INTEGER DEFAULT 0,
    thumbnail TEXT DEFAULT '',
    caption_status TEXT DEFAULT 'pending', -- pending | fetched | none | error
    caption_checked_at TEXT,
    ai_status TEXT DEFAULT 'todo',         -- todo | queued | done | failed | skip
    ai_error TEXT DEFAULT '',
    is_archive INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS transcripts (
    video_id TEXT PRIMARY KEY REFERENCES videos(id),
    raw TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    start REAL NOT NULL,
    end REAL NOT NULL,
    text TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    phon TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS passages_video ON passages(video_id, start);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    text, phon, content='passages', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS passages_ai AFTER INSERT ON passages BEGIN
    INSERT INTO passages_fts(rowid, text, phon) VALUES (new.id, new.text, new.phon);
END;
CREATE TRIGGER IF NOT EXISTS passages_ad AFTER DELETE ON passages BEGIN
    INSERT INTO passages_fts(passages_fts, rowid, text, phon) VALUES ('delete', old.id, old.text, old.phon);
END;
CREATE TRIGGER IF NOT EXISTS passages_au AFTER UPDATE ON passages BEGIN
    INSERT INTO passages_fts(passages_fts, rowid, text, phon) VALUES ('delete', old.id, old.text, old.phon);
    INSERT INTO passages_fts(rowid, text, phon) VALUES (new.id, new.text, new.phon);
END;
CREATE TABLE IF NOT EXISTS vectors (
    passage_id INTEGER PRIMARY KEY,
    vec BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
    video_id TEXT NOT NULL, start INTEGER NOT NULL, title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refs (
    video_id TEXT NOT NULL, start INTEGER NOT NULL, book TEXT NOT NULL, ref TEXT NOT NULL, label TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS concepts (
    video_id TEXT NOT NULL, start INTEGER NOT NULL, concept TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    kind TEXT NOT NULL,                    -- live | archive
    status TEXT NOT NULL DEFAULT 'pending',-- pending | approved | published | rejected | failed
    payload TEXT NOT NULL,
    chosen TEXT DEFAULT '{}',
    steps TEXT DEFAULT '{}',
    error TEXT DEFAULT '',
    created_at TEXT,
    decided_at TEXT,
    published_at TEXT
);
CREATE TABLE IF NOT EXISTS backups (
    video_id TEXT PRIMARY KEY, title TEXT, description TEXT, tags TEXT, saved_at TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    kind TEXT NOT NULL,                    -- pensee
    text TEXT NOT NULL,
    start INTEGER DEFAULT 0,
    status TEXT DEFAULT 'queued',          -- queued | sent
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS search_log (
    id INTEGER PRIMARY KEY, query TEXT, results INTEGER, mode TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS quota (day TEXT PRIMARY KEY, units INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS experiment (
    video_id TEXT PRIMARY KEY, grp TEXT NOT NULL, views_start INTEGER, started_at TEXT,
    views_last INTEGER, measured_at TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    kind TEXT NOT NULL,                    -- histoire | short | moment
    start REAL NOT NULL,
    end REAL NOT NULL,
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    hook TEXT DEFAULT '',
    format TEXT NOT NULL,                  -- vertical | horizontal
    status TEXT NOT NULL DEFAULT 'proposed', -- proposed | validated | rendering | rendered | uploading | posted | rejected | failed
    file TEXT DEFAULT '',
    youtube_id TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT,
    decided_at TEXT,
    posted_at TEXT
);
CREATE INDEX IF NOT EXISTS clips_status ON clips(status, kind);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, at TEXT, level TEXT, message TEXT
);
"""

_path: Path | None = None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def restore_seed(path: Path, seed: Path) -> None:
    """Au premier démarrage, part des données embarquées. Une base qui contient des transcripts n'est jamais remplacée."""
    if not seed.exists():
        return
    if path.exists():
        conn = sqlite3.connect(path)
        try:
            if conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]:
                return
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
        for leftover in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            leftover.unlink(missing_ok=True)
    with gzip.open(seed, "rb") as source, open(path, "wb") as target:
        shutil.copyfileobj(source, target)


def init(path: Path) -> None:
    global _path
    _path = path
    with connect() as c:
        c.executescript(SCHEMA)
        columns = {r["name"] for r in c.execute("PRAGMA table_info(videos)")}
        if "clips_status" not in columns:
            c.execute("ALTER TABLE videos ADD COLUMN clips_status TEXT DEFAULT 'todo'")


@contextmanager
def connect():
    conn = sqlite3.connect(_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log(message: str, level: str = "info") -> None:
    with connect() as c:
        c.execute("INSERT INTO events(at, level, message) VALUES (?,?,?)", (now(), level, message))
    print(f"[{level}] {message}".encode("ascii", "replace").decode(), flush=True)


def get_setting(key: str, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    with connect() as c:
        c.execute("INSERT INTO settings(key, value) VALUES (?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
