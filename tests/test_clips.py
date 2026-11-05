import dataclasses
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from studio import ai, clips, db, ingest, shabbat, youtube

VTT = """WEBVTT

00:00:10.000 --> 00:00:14.000
Un jour le Baal Chem Tov

00:00:14.000 --> 00:01:20.000
monte dans une charrette et il part.

00:01:20.000 --> 00:02:40.000
La suite de l'histoire { avec accolades }.
"""


@pytest.fixture(autouse=True)
def open_calendar(monkeypatch):
    monkeypatch.setattr(shabbat, "is_blocked", lambda s: False)


def seed():
    ingest.upsert_video({"id": "abc", "title": "Cours", "duration": 3000, "description": "Bloc de la chaîne"})
    ingest.store_transcript("abc", VTT, "test")


def story(start=12, end=150, title="La charrette", **extra):
    return {"start": start, "end": end, "title": title, "summary": "Une histoire.", "hook": "Un jour", **extra}


def rows():
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM clips ORDER BY id")]


class FakeYouTube:
    def __init__(self):
        self.uploads = []

    def upload_video(self, path, title, description, tags):
        self.uploads.append((path, title, description))
        return "nouvelle"


def fake_render(settings, clip):
    path = Path(settings.data_dir) / f"{clip['id']}.mp4"
    path.write_bytes(b"video")
    return path


def connected(settings):
    return dataclasses.replace(settings, google_client_id="a", google_client_secret="b", google_refresh_token="c")


def test_cuts_land_on_caption_lines(settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    clip = rows()[0]
    assert (clip["start"], clip["end"]) == (10, 160)
    assert clip["format"] == "horizontal"


def test_shorts_are_vertical_and_duplicates_skipped(settings):
    seed()
    assert clips.add("abc", "short", [story(20, 70), story(25, 75, "Doublon")], 3000) == 1
    assert rows()[0]["format"] == "vertical"


def test_same_passage_can_be_a_story_and_a_short(settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    assert clips.add("abc", "short", [story(20, 70)], 3000) == 1


def test_lengths_outside_limits_are_dropped(settings):
    seed()
    assert clips.add("abc", "short", [story(10, 14)], 3000) == 0
    assert clips.add("abc", "short", [story(10, 800)], 3000) == 0


def test_detection_marks_the_video_done(settings):
    seed()
    found = clips.store_detection("abc", {"stories": [story()], "moments": [], "shorts": [story(20, 70)]}, 3000)
    assert found == 2
    with db.connect() as c:
        assert c.execute("SELECT clips_status FROM videos").fetchone()[0] == "done"


def test_listing_filters(settings):
    seed()
    clips.store_detection("abc", {"stories": [story()], "moments": [], "shorts": [story(20, 70, "Court")]}, 3000)
    assert len(clips.listing(kind="histoire")["clips"]) == 1
    assert clips.listing(query="Court")["clips"][0]["kind"] == "short"
    data = clips.listing()
    assert data["kinds"] == {"histoire": 1, "short": 1} and data["lessons"] == 1
    assert data["clips"][0]["url"].startswith("https://youtu.be/abc?t=")


def test_validate_then_extract_then_post(settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    clip_id = rows()[0]["id"]
    clips.validate(clip_id, {"title": "Mon titre", "start": 15, "end": 100, "format": "horizontal"})
    api = FakeYouTube()
    clips.process(connected(settings), api, fake_render)
    clip = rows()[0]
    assert clip["status"] == "posted" and clip["youtube_id"] == "nouvelle" and clip["start"] == 15
    path, title, description = api.uploads[0]
    assert title == "Mon titre"
    assert "https://youtu.be/abc?t=15" in description and description.count("Bloc de la chaîne") == 1
    assert "#Shorts" not in description


def test_short_description_has_the_hashtag(settings):
    seed()
    clips.add("abc", "short", [story(20, 70)], 3000)
    assert "#Shorts" in clips.description(settings, clips.get(rows()[0]["id"]))


def test_nothing_posted_without_validation(settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    api = FakeYouTube()
    clips.process(connected(settings), api, fake_render)
    assert api.uploads == [] and rows()[0]["status"] == "proposed"


def test_nothing_happens_during_shabbat(settings, monkeypatch):
    monkeypatch.setattr(shabbat, "is_blocked", lambda s: True)
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    clips.validate(rows()[0]["id"])
    clips.process(connected(settings), FakeYouTube(), lambda *a: pytest.fail("extraction pendant Chabbat"))
    assert rows()[0]["status"] == "validated"


def test_without_youtube_the_clip_waits_once_extracted(settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    clips.validate(rows()[0]["id"])
    clips.process(settings, None, fake_render)
    assert rows()[0]["status"] == "rendered"
    api = FakeYouTube()
    clips.process(connected(settings), api, lambda *a: pytest.fail("extraction refaite"))
    assert rows()[0]["status"] == "posted"


def test_quota_keeps_the_clip_for_tomorrow(settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    clips.validate(rows()[0]["id"])
    youtube.record(settings.youtube_daily_quota)
    api = FakeYouTube()
    clips.process(connected(settings), api, fake_render)
    assert rows()[0]["status"] == "rendered" and api.uploads == []


def test_failure_is_shown_and_can_be_retried(settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    clip_id = rows()[0]["id"]
    clips.validate(clip_id)

    def broken(*args):
        raise RuntimeError("YouTube refuse")

    clips.process(settings, None, broken)
    assert rows()[0]["status"] == "failed" and "refuse" in rows()[0]["error"]
    clips.validate(clip_id)
    clips.process(settings, None, fake_render)
    assert rows()[0]["status"] == "rendered" and rows()[0]["error"] == ""


def test_too_long_for_a_short_is_refused(settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    with pytest.raises(ValueError):
        clips.validate(rows()[0]["id"], {"start": 0, "end": 400, "format": "vertical"})


def _ffmpeg_or_skip():
    try:
        return clips._ffmpeg()
    except RuntimeError:
        pytest.skip("ffmpeg absent")


@pytest.mark.parametrize("source, shape, expected", [
    ("360x640", "horizontal", "360,640"),
    ("640x360", "horizontal", "640,360"),
    ("640x360", "vertical", "1080,1920"),
    ("360x640", "vertical", "1080,1920"),
])
def test_real_edit_from_a_local_file(settings, source, shape, expected):
    ffmpeg = _ffmpeg_or_skip()
    seed()
    folder = settings.data_dir / "sources"
    folder.mkdir()
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={source}:rate=10:duration=30", "-f",
                    "lavfi", "-i", "sine=frequency=440:duration=30", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(folder / "abc.mp4")], check=True, capture_output=True)
    clip = {"id": f"t-{source}-{shape}", "video_id": "abc", "start": 12, "end": 20, "format": shape, "title": "Titre", "hook": "Accroche"}
    path = clips.render(settings, clip)
    probe = str(Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe")))
    size = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of",
                           "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    length = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True).stdout.strip()
    assert size == expected
    assert 7 < float(length) < 9.5


def test_bot_check_gives_a_clear_message(settings, monkeypatch):
    _ffmpeg_or_skip()
    seed()
    refused = SimpleNamespace(returncode=1, stderr="ERROR: [youtube] abc: Sign in to confirm you're not a bot.")
    monkeypatch.setattr(clips.subprocess, "run", lambda *a, **k: refused)
    with pytest.raises(RuntimeError, match="data/sources/abc.mp4"):
        clips.render(settings, {"id": 1, "video_id": "abc", "start": 1, "end": 9, "format": "horizontal", "title": "T", "hook": ""})


def test_subtitles_file_is_safe():
    cues = [{"start": 80, "end": 160, "text": "La suite { avec accolades }."}]
    ass = clips.build_ass(cues, 60, 170, "Accroche {x}", "horizontal")
    assert "PlayResX: 1280" in ass and "Accroche x" in ass and "{" not in ass.split("[Events]")[1]
    assert "0:00:20.00" in ass


def test_fine_transcript_keeps_times_every_ten_seconds():
    cues = [{"start": i * 4.0, "end": i * 4.0 + 4, "text": f"mot{i}"} for i in range(8)]
    lines = ai.fine_transcript(cues).splitlines()
    assert lines[0] == "[0] mot0 mot1 mot2" and lines[1].startswith("[12]")


def test_detection_request(settings):
    captured = {}

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            text = json.dumps({"stories": [], "moments": [], "shorts": []})
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    api = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: captured.update(kw) or Stream())))
    video = {"title": "Cours", "duration": 100}
    assert ai.detect_clips(settings, video, [{"start": 1, "end": 2, "text": "bonjour"}], api=api)["stories"] == []
    assert captured["fallbacks"] == "default"
    assert captured["output_config"]["format"]["schema"] == ai.CLIP_SCHEMA


@pytest.fixture
def client(settings, monkeypatch):
    from studio import app as module
    db.init(settings.db_path)
    monkeypatch.setattr(module, "settings", dataclasses.replace(settings, admin_password="secret"))
    monkeypatch.setattr(module, "_start_processing", lambda: None)
    return TestClient(module.app, base_url="https://testserver")


def test_clip_pages_need_login(client, settings):
    seed()
    clips.add("abc", "histoire", [story()], 3000)
    clip_id = rows()[0]["id"]
    assert client.get("/api/clips").status_code == 401
    assert client.get(f"/api/clips/{clip_id}/text").status_code == 401
    assert client.post(f"/api/clips/{clip_id}/validate", json={"fields": {}}).status_code == 401
    assert client.post("/api/clips/validate-many", json={"ids": [clip_id]}).status_code == 401
    assert rows()[0]["status"] == "proposed"


def test_clip_api_flow(client, settings):
    seed()
    clips.store_detection("abc", {"stories": [story()], "moments": [], "shorts": [story(20, 70)]}, 3000)
    client.post("/api/login", json={"password": "secret"})
    listed = client.get("/api/clips", params={"kind": "histoire"}).json()
    clip_id = listed["clips"][0]["id"]
    assert "charrette" in client.get(f"/api/clips/{clip_id}/text").json()["text"]
    assert client.post(f"/api/clips/{clip_id}/validate", json={"fields": {"title": "Titre"}}).status_code == 200
    assert client.get("/api/clips", params={"status": "active"}).json()["clips"][0]["title"] == "Titre"
    short_id = client.get("/api/clips", params={"kind": "short"}).json()["clips"][0]["id"]
    assert client.post(f"/api/clips/{short_id}/reject", json={}).status_code == 200
    assert client.post(f"/api/clips/{short_id}/reopen", json={}).status_code == 200
    assert client.post("/api/clips/validate-many", json={"ids": [short_id]}).json()["count"] == 1
    bad = client.post(f"/api/clips/{short_id}/validate", json={"fields": {"format": "carré"}})
    assert bad.status_code == 400
