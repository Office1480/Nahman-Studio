from studio import db, embeddings, glossary, ingest, search

VTT = """WEBVTT

00:00:10.000 --> 00:00:40.000
Rabbi Nahman a révélé le tikoun aklali pour réparer l'âme.

00:10:00.000 --> 00:10:30.000
Il faut sortir de la tristesse et chercher le bon point en soi.

00:20:00.000 --> 00:20:30.000
Un tadic nous apprend la foi.
"""


def seed():
    ingest.upsert_video({"id": "abc", "title": "Cours", "duration": 3000, "views": 10})
    ingest.store_transcript("abc", VTT, "test")


def run(query, monkeypatch):
    monkeypatch.setattr(embeddings, "search", lambda *a, **k: [])
    return search.search(query, "", log=True)


def test_spelling_variants_reach_the_same_key():
    assert glossary.phon("Tikkoun Haklali") == glossary.phon("tikoun aklali") == glossary.phon("tiqoun haklali")


def test_finds_concept_with_other_spelling_and_gives_exact_link(settings, monkeypatch):
    seed()
    result = run("tikkun haklali", monkeypatch)
    moment = result["results"][0]["moments"][0]
    assert result["concepts"][0]["id"] == "tikoun-haklali"
    assert moment["url"] == "https://youtu.be/abc?t=10"
    assert moment["time"] == "00:10"
    assert "<mark>" in moment["html"]


def test_synonym_finds_concept(settings, monkeypatch):
    seed()
    assert run("les 10 psaumes", monkeypatch)["results"][0]["moments"][0]["start"] == 10


def test_known_caption_errors_are_fixed_when_stored(settings, monkeypatch):
    seed()
    assert run("tsadik", monkeypatch)["results"][0]["moments"][0]["start"] == 1200


def test_word_outside_glossary(settings, monkeypatch):
    seed()
    assert run("réparer âme", monkeypatch)["results"]


def test_unknown_subject_is_logged_as_missing(settings, monkeypatch):
    seed()
    assert run("astrologie", monkeypatch)["results"] == []
    assert search.missing_topics()[0]["query"] == "astrologie"


def test_html_in_transcript_is_escaped():
    assert "<script>" not in search.mark("<script>alert(1)</script>", set())


def test_corrections_update_search(settings, monkeypatch):
    seed()
    ingest.apply_corrections("abc", [{"wrong": "le bon point", "right": "la Nekouda Tova"}])
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM passages WHERE text LIKE '%Nekouda%'").fetchone()[0] == 1
    assert run("nekouda", monkeypatch)["results"]
