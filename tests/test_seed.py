import gzip
import shutil

from studio import db, ingest


def make_seed(settings, tmp_path):
    ingest.upsert_video({"id": "abc", "title": "Cours"})
    ingest.store_transcript("abc", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nbonjour\n", "test")
    seed = tmp_path / "seed.db.gz"
    with open(settings.db_path, "rb") as source, gzip.open(seed, "wb") as target:
        shutil.copyfileobj(source, target)
    return seed


def count():
    with db.connect() as c:
        return c.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]


def test_empty_database_is_replaced_by_the_seed(settings, tmp_path):
    seed = make_seed(settings, tmp_path)
    empty = tmp_path / "vide" / "studio.db"
    empty.parent.mkdir()
    db.init(empty)  # le démarrage réel crée la base avant que l'application ne la restaure
    assert count() == 0
    db.restore_seed(empty, seed)
    db.init(empty)
    assert count() == 1


def test_missing_database_is_restored(settings, tmp_path):
    seed = make_seed(settings, tmp_path)
    fresh = tmp_path / "neuf.db"
    db.restore_seed(fresh, seed)
    db.init(fresh)
    assert count() == 1


def test_real_data_is_never_overwritten(settings, tmp_path):
    seed = make_seed(settings, tmp_path)
    ingest.upsert_video({"id": "autre", "title": "Autre"})
    ingest.store_transcript("autre", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsalut\n", "test")
    db.restore_seed(settings.db_path, seed)
    db.init(settings.db_path)
    assert count() == 2
