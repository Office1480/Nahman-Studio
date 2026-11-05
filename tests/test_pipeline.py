import copy
import dataclasses
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from conftest import PAYLOAD
from studio import ai, db, ingest, pipeline, shabbat, telegram, thumbnails, youtube

VTT = "WEBVTT\n\n00:00:10.000 --> 00:00:40.000\nle tique un klali\n"


def seed(video_id="abc", archive=False):
    ingest.upsert_video({"id": video_id, "title": "Vieux titre", "description": "Vieille description", "duration": 3000,
                         "views": 10, "tags": ["ancien"]}, is_archive=archive)
    ingest.store_transcript(video_id, VTT, "test")


class FakeYouTube:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def _record(self, name, *args):
        if name == self.fail_on:
            raise youtube.QuotaExceeded("quota")
        self.calls.append((name, *args))

    def update_metadata(self, *args):
        self._record("metadata", *args)

    def upsert_caption(self, *args):
        self._record("captions", *args)

    def set_thumbnail(self, *args):
        self._record("thumbnail", *args)


def proposal(pid):
    with db.connect() as c:
        return dict(c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone())


def test_normalize_respects_youtube_rules():
    data = ai.normalize(copy.deepcopy(PAYLOAD), 3000)
    assert data["chapters"][0] == {"start": 0, "title": "Introduction"}
    assert [c["start"] for c in data["chapters"]] == [0, 30, 300]
    assert len(data["shorts"]) == 1


def test_description_stays_under_the_limit():
    text = ai.compose_description("é" * 6000, [{"start": s, "title": "x"} for s in (0, 60, 120)], ["#breslev"], "Pied")
    assert len(text.encode()) <= 5000
    assert "00:00 x" in text and "Pied" in text


def test_schema_is_strict_everywhere():
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
    check(ai.VIDEO_SCHEMA)
    check(ai.ANSWER_SCHEMA)


def test_generate_sends_expected_request(settings):
    captured = {}

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(PAYLOAD))])

    api = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        stream=lambda **kw: captured.update(kw) or Stream())))
    video = {"title": "Cours", "duration": 100, "description": ""}
    result = ai.generate(settings, video, [{"start": 12.4, "text": "bonjour"}], note="plus court", api=api)
    assert result["titles"] == PAYLOAD["titles"]
    assert captured["model"] == "claude-opus-5-5"
    assert captured["fallbacks"] == "default"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert "[12] bonjour" in captured["messages"][0]["content"]
    assert "plus court" in captured["messages"][0]["content"]


@pytest.mark.parametrize("reason", ["refusal", "max_tokens"])
def test_incomplete_answers_are_rejected(reason):
    with pytest.raises(RuntimeError):
        ai._parse(SimpleNamespace(stop_reason=reason, content=[]))


def test_proposal_then_publication_with_backup(settings, monkeypatch):
    monkeypatch.setattr(thumbnails, "make", lambda *a: b"png")
    seed()
    pid = pipeline.create_proposal(settings, "abc", copy.deepcopy(PAYLOAD))
    with db.connect() as c:
        assert "Tikoun" in c.execute("SELECT text FROM passages").fetchone()["text"]
        assert c.execute("SELECT COUNT(*) FROM refs").fetchone()[0] == 1

    payload = json.loads(proposal(pid)["payload"])
    pipeline.approve(pid, {**pipeline.default_choice(payload, 1), "thumbnail": True})
    api = FakeYouTube()
    assert pipeline.publish(settings, proposal(pid), api)

    assert [c[0] for c in api.calls] == ["metadata", "captions", "thumbnail"]
    metadata = api.calls[0]
    assert metadata[2] == PAYLOAD["titles"][1]
    assert "Chapitres :" in metadata[3] and "https://exemple.test/cours/abc" in metadata[3]
    assert metadata[3].endswith("Vieille description")
    assert set(metadata[5]) == {"en", "he", "es"}
    assert proposal(pid)["status"] == "published"
    with db.connect() as c:
        assert c.execute("SELECT title FROM backups").fetchone()["title"] == "Vieux titre"
        assert c.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1

    pipeline.restore(settings, "abc")
    with db.connect() as c:
        assert c.execute("SELECT title FROM videos").fetchone()["title"] == "Vieux titre"


def test_thumbnail_is_kept_unless_asked(settings):
    seed()
    pid = pipeline.create_proposal(settings, "abc", copy.deepcopy(PAYLOAD))
    pipeline.approve(pid, pipeline.default_choice(json.loads(proposal(pid)["payload"])))
    api = FakeYouTube()
    pipeline.publish(settings, proposal(pid), api)
    assert [c[0] for c in api.calls] == ["metadata", "captions"]
    assert proposal(pid)["status"] == "published"


def test_quota_stop_resumes_without_repeating(settings, monkeypatch):
    monkeypatch.setattr(thumbnails, "make", lambda *a: b"png")
    seed()
    pid = pipeline.create_proposal(settings, "abc", copy.deepcopy(PAYLOAD))
    pipeline.approve(pid, {**pipeline.default_choice(json.loads(proposal(pid)["payload"])), "thumbnail": True})
    first = FakeYouTube(fail_on="captions")
    with pytest.raises(youtube.QuotaExceeded):
        pipeline.publish(settings, proposal(pid), first)
    assert proposal(pid)["status"] == "approved"
    second = FakeYouTube()
    pipeline.publish(settings, proposal(pid), second)
    assert [c[0] for c in second.calls] == ["captions", "thumbnail"]
    assert proposal(pid)["status"] == "published"


def test_nothing_published_during_shabbat(settings, monkeypatch):
    configured = dataclasses.replace(settings, google_client_id="a", google_client_secret="b", google_refresh_token="c")
    monkeypatch.setattr(shabbat, "is_blocked", lambda s: True)
    monkeypatch.setattr(pipeline, "publish", lambda *a, **k: pytest.fail("publication pendant Chabbat"))
    seed()
    pid = pipeline.create_proposal(settings, "abc", copy.deepcopy(PAYLOAD))
    pipeline.approve(pid, pipeline.default_choice(json.loads(proposal(pid)["payload"])))
    pipeline.run_publish(configured)
    assert proposal(pid)["status"] == "approved"


def test_shabbat_windows():
    items = [{"category": "candles", "date": "2026-11-06T16:10:00+02:00"},
             {"category": "parashat", "date": "2026-11-07"},
             {"category": "havdalah", "date": "2026-11-07T17:25:00+02:00"}]
    (start, end), = shabbat.parse_windows(items)
    assert start.hour == 16 and end.hour == 17


def test_fail_safe_without_calendar(settings, monkeypatch):
    monkeypatch.setattr(shabbat, "_windows", lambda s: None)
    saturday = datetime(2026, 11, 7, 10, tzinfo=timezone.utc)
    tuesday = datetime(2026, 11, 3, 10, tzinfo=timezone.utc)
    assert shabbat.is_blocked(settings, saturday)
    assert not shabbat.is_blocked(settings, tuesday)


def test_captions_prefer_manual_and_skip_our_track():
    tracks = [{"id": "a", "language": "fr", "status": "serving", "trackKind": "asr"},
              {"id": "b", "language": "fr", "status": "serving", "trackKind": "standard"},
              {"id": "c", "language": "fr", "status": "serving", "name": pipeline.CAPTION_TRACK},
              {"id": "d", "language": "en", "status": "serving"}]
    assert pipeline.pick_track(tracks)["id"] == "b"
    assert pipeline.pick_track([{"language": "fr", "status": "syncing"}]) is None


def test_first_run_does_not_flood(settings, monkeypatch):
    feed = [{"id": f"v{i}", "title": "x", "published_at": None, "thumbnail": ""} for i in range(3)]
    monkeypatch.setattr(pipeline, "rss_videos", lambda *a: feed)
    assert pipeline.discover(settings) == []
    monkeypatch.setattr(pipeline, "rss_videos", lambda *a: feed + [{"id": "new", "title": "Nouveau", "published_at": None, "thumbnail": ""}])
    assert pipeline.discover(settings) == ["new"]
    with db.connect() as c:
        assert c.execute("SELECT is_archive FROM videos WHERE id='new'").fetchone()[0] == 0
        assert c.execute("SELECT is_archive FROM videos WHERE id='v0'").fetchone()[0] == 1


def test_telegram_refuses_strangers(settings):
    configured = dataclasses.replace(settings, telegram_chat_id="42", telegram_bot_token="t")
    seed()
    pid = pipeline.create_proposal(settings, "abc", copy.deepcopy(PAYLOAD))
    answers = []
    bot = SimpleNamespace(answer=lambda i, t: answers.append(t), clear_buttons=lambda *a: None)
    update = {"callback_query": {"id": "1", "from": {"id": 99}, "data": f"ok:{pid}:0"}}
    pipeline.handle_telegram(configured, bot, update)
    assert proposal(pid)["status"] == "pending"
    update["callback_query"]["from"]["id"] = 42
    pipeline.handle_telegram(configured, bot, update)
    assert proposal(pid)["status"] == "approved"
    assert answers[0] == "Non autorisé"


def test_quota_budget_keeps_a_reserve_for_new_lessons(settings):
    youtube.record(settings.youtube_daily_quota - settings.live_quota_reserve)
    assert not youtube.can_spend(settings, 50, archive=True)
    assert youtube.can_spend(settings, 50)


def test_control_group_is_left_alone(settings):
    for i in range(6):
        ingest.upsert_video({"id": f"v{i}", "title": "x", "views": 100 - i}, is_archive=True)
    pipeline.setup_experiment()
    candidates = pipeline.archive_candidates(10)
    assert candidates == ["v0", "v2", "v4"]


def test_iso_duration():
    assert youtube.parse_duration("PT1H45M30S") == 6330
    assert youtube.parse_duration("P0D") == 0


def test_thumbnail_compose():
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (480, 360), (40, 80, 120)).save(buffer, "JPEG")
    image = Image.open(io.BytesIO(thumbnails.compose(buffer.getvalue(), "Le Tikoun Haklali")))
    assert image.size == (1280, 720)


def test_telegram_message_has_title_buttons(settings):
    payload = ai.normalize(copy.deepcopy(PAYLOAD), 3000)
    text, buttons = telegram.proposal_message(settings, 7, {"title": "<x>"}, payload)
    assert "&lt;x&gt;" in text
    assert buttons[0][1]["callback_data"] == "ok:7:1"
