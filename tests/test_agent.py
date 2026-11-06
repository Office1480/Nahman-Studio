import dataclasses
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import agent, clips, config, db, ingest, shabbat

VTT = "WEBVTT\n\n00:00:10.000 --> 00:01:20.000\nUn jour le Baal Chem Tov\n\n00:02:00.000 --> 00:02:10.000\nHors passage\n"
HEADERS = {"X-Agent-Token": "jeton"}


@pytest.fixture(autouse=True)
def open_calendar(monkeypatch):
    monkeypatch.setattr(shabbat, "is_blocked", lambda s: False)


@pytest.fixture
def server(settings, monkeypatch):
    from studio import app as module
    db.init(settings.db_path)
    configured = dataclasses.replace(settings, admin_password="secret", agent_token="jeton")
    monkeypatch.setattr(module, "settings", configured)
    monkeypatch.setattr(module, "_start_processing", lambda: None)
    restarts = []
    monkeypatch.setattr(module, "_restart", lambda: restarts.append(1))
    client = TestClient(module.app, base_url="https://testserver")
    client.restarts = restarts
    client.settings = configured
    return client


def seed(validated=True):
    ingest.upsert_video({"id": "abc", "title": "Cours", "duration": 3000})
    ingest.store_transcript("abc", VTT, "test")
    clips.add("abc", "histoire", [{"start": 10, "end": 80, "title": "Histoire"}], 3000)
    with db.connect() as c:
        clip_id = c.execute("SELECT id FROM clips").fetchone()[0]
    if validated:
        clips.validate(clip_id)
    return clip_id


def status(clip_id):
    return clips.get(clip_id)["status"]


@pytest.mark.parametrize("headers", [{}, {"X-Agent-Token": "faux"}])
def test_agent_needs_its_token(server, headers):
    clip_id = seed()
    assert server.get("/api/agent/next", headers=headers).status_code == 401
    assert server.post(f"/api/agent/{clip_id}/file", content=b"x", headers=headers).status_code == 401
    assert server.post(f"/api/agent/{clip_id}/fail", json={"error": "x", "retry": False}, headers=headers).status_code == 401
    assert status(clip_id) == "validated"


def test_no_token_configured_means_no_agent(server, monkeypatch):
    from studio import app as module
    monkeypatch.setattr(module, "settings", dataclasses.replace(server.settings, agent_token=""))
    assert server.get("/api/agent/next", headers={"X-Agent-Token": ""}).status_code == 401


def test_agent_gets_the_clip_once_with_its_subtitles(server):
    clip_id = seed()
    job = server.get("/api/agent/next", headers=HEADERS).json()["clip"]
    assert job["id"] == clip_id and status(clip_id) == "rendering"
    assert [c["text"] for c in job["cues"]] == ["Un jour le Baal Chem Tov"]
    assert server.get("/api/agent/next", headers=HEADERS).json()["clip"] is None


def test_only_validated_clips_are_handed_out(server):
    seed(validated=False)
    assert server.get("/api/agent/next", headers=HEADERS).json()["clip"] is None


def test_nothing_handed_out_during_shabbat(server, monkeypatch):
    monkeypatch.setattr(shabbat, "is_blocked", lambda s: True)
    seed()
    assert server.get("/api/agent/next", headers=HEADERS).json()["clip"] is None


def test_abandoned_job_goes_back_in_the_queue(server):
    clip_id = seed()
    server.get("/api/agent/next", headers=HEADERS)
    with db.connect() as c:
        c.execute("UPDATE clips SET claimed_at='2020-01-01T00:00:00Z' WHERE id=?", (clip_id,))
    assert server.get("/api/agent/next", headers=HEADERS).json()["clip"]["id"] == clip_id


def test_upload_stores_the_video(server):
    clip_id = seed()
    server.get("/api/agent/next", headers=HEADERS)
    assert server.post(f"/api/agent/{clip_id}/file", content=b"video", headers=HEADERS).status_code == 200
    assert status(clip_id) == "rendered" and clips.get(clip_id)["has_file"]


def test_server_never_extracts_when_the_pc_does_it(server):
    clip_id = seed()
    clips.process(server.settings, None, lambda *a: pytest.fail("extraction sur le serveur"))
    assert status(clip_id) == "validated"


def _agent_client(server):
    class Bridge(httpx.BaseTransport):
        def handle_request(self, request):
            answer = server.request(request.method, request.url.path, content=request.read(),
                                    headers={"Content-Type": request.headers.get("content-type", ""), **HEADERS})
            return httpx.Response(answer.status_code, content=answer.content, headers=dict(answer.headers))
    return httpx.Client(transport=Bridge(), base_url="https://testserver")


def test_full_round_trip(server, tmp_path):
    clip_id = seed()

    def make(settings, clip, cues):
        assert cues
        folder = tmp_path / "travail"
        folder.mkdir()
        path = folder / "clip.mp4"
        path.write_bytes(b"video")
        return path

    assert agent.run_once(server.settings, _agent_client(server), make) == "fait"
    assert status(clip_id) == "rendered"
    assert not (tmp_path / "travail").exists()
    assert agent.run_once(server.settings, _agent_client(server), make) == "rien"


def test_bot_check_puts_the_clip_back_in_the_queue(server):
    clip_id = seed()

    def refused(*args):
        raise clips.BotCheck("anti-robots")

    assert agent.run_once(server.settings, _agent_client(server), refused) == "refus"
    clip = clips.get(clip_id)
    assert clip["status"] == "validated" and "anti-robots" in clip["error"]


def test_other_errors_are_shown_as_failures(server):
    clip_id = seed()

    def broken(*args):
        raise ValueError("montage cassé")

    assert agent.run_once(server.settings, _agent_client(server), broken) == "échec"
    assert status(clip_id) == "failed"


def test_agent_presence(server):
    assert clips.agent_status(server.settings)["online"] is False
    server.get("/api/agent/next", headers=HEADERS)
    assert clips.agent_status(server.settings)["online"] is True


def test_keys_are_saved_but_never_returned(server):
    assert server.post("/api/connections", json={"values": {"ANTHROPIC_API_KEY": "cle"}}).status_code == 401
    server.post("/api/login", json={"password": "secret"})
    server.post("/api/connections", json={"values": {"ANTHROPIC_API_KEY": " cle ", "PATH": "x", "GOOGLE_REFRESH_TOKEN": "y"}})
    stored = (Path(server.settings.data_dir) / "secrets.env").read_text(encoding="utf-8")
    assert stored == "ANTHROPIC_API_KEY=cle\n"
    assert server.restarts == [1]
    assert "cle" not in server.get("/api/connections").text


def test_saved_keys_are_loaded_at_start(settings, monkeypatch):
    config.save_secrets(settings.data_dir, {"ANTHROPIC_API_KEY": "cle"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.load().has_claude


def test_google_callback_checks_the_state(server, monkeypatch):
    from studio import youtube
    monkeypatch.setattr(youtube, "exchange_code", lambda *a: pytest.fail("code accepté sans vérification"))
    assert "pas abouti" in server.get("/auth/google/callback", params={"code": "c", "state": "faux"}).text
    db.set_setting("google_state", "bon")
    assert "pas abouti" in server.get("/auth/google/callback", params={"code": "c", "state": "faux"}).text
    monkeypatch.setattr(youtube, "exchange_code", lambda *a: "jeton-google")
    assert "connectée" in server.get("/auth/google/callback", params={"code": "c", "state": "bon"}).text
    assert "GOOGLE_REFRESH_TOKEN=jeton-google" in (Path(server.settings.data_dir) / "secrets.env").read_text(encoding="utf-8")
    assert "pas abouti" in server.get("/auth/google/callback", params={"code": "c", "state": "bon"}).text


def test_google_start_needs_login(server):
    assert server.get("/auth/google/start", follow_redirects=False).status_code == 401
