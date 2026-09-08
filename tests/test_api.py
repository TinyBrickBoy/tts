"""API tests. They use stub engines so no network access is required."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import engines as engines_module
from app import main as main_module
from app.config import settings
from app.engines import Engine, TTSError, Voice
from app.main import _slug, app


class StubEngine(Engine):
    """Records how often it was asked to render something."""

    name = "stub"
    media_type = "audio/mpeg"
    extension = "mp3"
    default_voice = "stub-voice"

    def __init__(self, name: str = "stub", fails: bool = False) -> None:
        self.name = name
        self.fails = fails
        self.calls = 0

    async def voices(self) -> list[Voice]:
        if self.fails:
            raise TTSError("no catalogue")
        return [Voice(id="stub-voice", label="Stub", locale="de-DE", gender="Female")]

    async def synthesize(self, text: str, voice: str, rate: int, pitch: int) -> bytes:
        self.calls += 1
        if self.fails:
            raise TTSError("stub is broken")
        return f"{voice}|{rate}|{pitch}|{text}".encode()


@pytest.fixture
def client(monkeypatch):
    """Client backed by a single working stub engine and no fallback."""
    stub = StubEngine()
    monkeypatch.setattr(engines_module, "ENGINES", {"stub": stub})
    monkeypatch.setattr(settings, "default_engine", "stub")
    monkeypatch.setattr(settings, "fallback_engine", "")
    monkeypatch.setattr(settings, "default_voice", "")
    main_module._cache.clear()
    with TestClient(app) as test_client:
        test_client.stub = stub
        yield test_client


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Simple TTS" in response.text


def test_engines_are_listed(client):
    body = client.get("/api/engines").json()
    assert body["default"] == "stub"
    assert [engine["name"] for engine in body["engines"]] == ["stub"]


def test_voices_are_listed_and_filtered(client):
    assert client.get("/api/voices").json()["count"] == 1
    assert client.get("/api/voices", params={"locale": "de"}).json()["count"] == 1
    assert client.get("/api/voices", params={"locale": "fr"}).json()["count"] == 0


def test_post_returns_audio_with_metadata_headers(client):
    response = client.post("/api/tts", json={"text": "Hallo Welt", "rate": 10, "pitch": -5})
    assert response.status_code == 200
    assert response.content == b"stub-voice|10|-5|Hallo Welt"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-tts-engine"] == "stub"
    assert response.headers["x-tts-voice"] == "stub-voice"
    assert 'inline; filename="hallo-welt.mp3"' == response.headers["content-disposition"]


def test_get_route_supports_download_disposition(client):
    response = client.get("/api/tts", params={"text": "Hallo", "download": "true"})
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")


def test_identical_requests_are_served_from_cache(client):
    for _ in range(3):
        assert client.post("/api/tts", json={"text": "gleich"}).status_code == 200
    assert client.stub.calls == 1
    assert client.post("/api/tts", json={"text": "anders"}).status_code == 200
    assert client.stub.calls == 2


def test_blank_text_is_rejected(client):
    assert client.post("/api/tts", json={"text": "   "}).status_code == 422
    assert client.post("/api/tts", json={"text": ""}).status_code == 422


def test_text_over_the_limit_is_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "max_chars", 5)
    assert client.post("/api/tts", json={"text": "viel zu lang"}).status_code == 413


def test_unknown_engine_is_rejected(client):
    response = client.post("/api/tts", json={"text": "Hallo", "engine": "nope"})
    assert response.status_code == 400
    assert "unknown engine" in response.json()["detail"]


def test_out_of_range_rate_is_rejected(client):
    assert client.post("/api/tts", json={"text": "Hallo", "rate": 999}).status_code == 422


def test_failing_engine_reports_bad_gateway(client, monkeypatch):
    monkeypatch.setattr(engines_module, "ENGINES", {"stub": StubEngine(fails=True)})
    response = client.post("/api/tts", json={"text": "Hallo"})
    assert response.status_code == 502
    assert "stub is broken" in response.json()["detail"]


def test_fallback_engine_takes_over(monkeypatch):
    broken = StubEngine(name="broken", fails=True)
    backup = StubEngine(name="backup")
    monkeypatch.setattr(engines_module, "ENGINES", {"broken": broken, "backup": backup})
    monkeypatch.setattr(settings, "default_engine", "broken")
    monkeypatch.setattr(settings, "fallback_engine", "backup")
    monkeypatch.setattr(settings, "default_voice", "")
    main_module._cache.clear()
    with TestClient(app) as test_client:
        response = test_client.post("/api/tts", json={"text": "Hallo"})
    assert response.status_code == 200
    assert response.headers["x-tts-engine"] == "backup"
    assert backup.calls == 1


def test_explicitly_chosen_engine_does_not_fall_back(monkeypatch):
    broken = StubEngine(name="broken", fails=True)
    backup = StubEngine(name="backup")
    monkeypatch.setattr(engines_module, "ENGINES", {"broken": broken, "backup": backup})
    monkeypatch.setattr(settings, "default_engine", "broken")
    monkeypatch.setattr(settings, "fallback_engine", "backup")
    main_module._cache.clear()
    with TestClient(app) as test_client:
        response = test_client.post("/api/tts", json={"text": "Hallo", "engine": "broken"})
    assert response.status_code == 502
    assert backup.calls == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hallo Welt", "hallo-welt"),
        ("Grüße aus Köln!", "gruesse-aus-koeln"),
        ("...", "speech"),
        ("a" * 80, "a" * 40),
    ],
)
def test_slug_builds_safe_file_names(text, expected):
    assert _slug(text) == expected
