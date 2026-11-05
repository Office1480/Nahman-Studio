import copy
import dataclasses

import pytest
from fastapi.testclient import TestClient

from conftest import PAYLOAD
from studio import db, ingest, pipeline


@pytest.fixture
def client(settings, monkeypatch):
    from studio import app as module
    db.init(settings.db_path)
    monkeypatch.setattr(module, "settings", dataclasses.replace(settings, admin_password="secret"))
    return TestClient(module.app, base_url="https://testserver")


def seed(settings):
    ingest.upsert_video({"id": "abc", "title": "Cours <b>", "duration": 3000, "views": 5})
    ingest.store_transcript("abc", "WEBVTT\n\n00:00:10.000 --> 00:00:40.000\nle tikoun aklali\n", "test")
    return pipeline.create_proposal(settings, "abc", copy.deepcopy(PAYLOAD))


@pytest.mark.parametrize("path", ["/api/status", "/api/proposals", "/api/settings", "/api/archives", "/api/diffusion",
                                  "/api/insights"])
def test_dashboard_needs_login(client, path):
    assert client.get(path).status_code == 401


def test_actions_need_login(client, settings):
    pid = seed(settings)
    assert client.post(f"/api/proposals/{pid}/approve", json={"chosen": {}}).status_code == 401
    assert client.post("/api/settings", json={"values": {"archive_enabled": True}}).status_code == 401
    assert client.post("/api/tick").status_code == 401


def test_login(client):
    assert client.post("/api/login", json={"password": "faux"}).status_code == 401
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/me").json()["admin"] is True


def test_no_password_means_no_dashboard(client, monkeypatch, settings):
    from studio import app as module
    monkeypatch.setattr(module, "settings", dataclasses.replace(settings, admin_password=""))
    assert client.post("/api/login", json={"password": ""}).status_code == 401


def test_public_search_and_lesson_page(client, settings):
    seed(settings)
    found = client.get("/api/search", params={"q": "tikkun haklali"}).json()
    assert found["results"][0]["moments"][0]["url"] == "https://youtu.be/abc?t=10"
    page = client.get("/cours/abc").text
    assert "Cours &lt;b&gt;" in page and "tikoun aklali" in page
    assert client.get("/cours/inconnu").status_code == 404


def test_validate_through_api(client, settings):
    pid = seed(settings)
    client.post("/api/login", json={"password": "secret"})
    pending = client.get("/api/proposals", params={"status": "pending"}).json()
    assert pending[0]["payload"]["chapters"][0]["time"] == "00:00"
    chosen = {"title": "Mon titre", "description": "d", "tags": [], "chapters": [], "thumbnail": False, "thumbnail_text": ""}
    assert client.post(f"/api/proposals/{pid}/approve", json={"chosen": chosen}).status_code == 200
    assert client.get("/api/proposals", params={"status": "approved"}).json()[0]["chosen"]["title"] == "Mon titre"


def test_question_without_claude_gives_passages(client, settings):
    seed(settings)
    answer = client.post("/api/ask", json={"question": "Que dit le Rav sur le tikoun haklali ?"}).json()
    assert answer["mode"] == "extraits" and answer["sources"]
