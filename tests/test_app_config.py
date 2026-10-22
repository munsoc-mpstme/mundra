"""GET /public/app_config: the app's update prompt settings from data/app_config.json."""

import json
from pathlib import Path

import pytest

import app as app_module

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "app_config.json"


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Points the route at a scratch file; call it with the file's contents (a dict, or
    raw text for a broken file)."""
    path = tmp_path / "app_config.json"
    monkeypatch.setattr(app_module, "APP_CONFIG_FILE_PATH", str(path))
    app_module.read_app_config.cache_clear()

    def write(content):
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        app_module.read_app_config.cache_clear()

    yield write
    app_module.read_app_config.cache_clear()


def platform(latest="3.1.0", store_url="https://example.com/app"):
    return {"latest": latest, "store_url": store_url}


def test_the_committed_file_is_valid():
    """A bad edit to the real file would silently turn the prompt off; catch it here."""
    data = json.loads(CONFIG_PATH.read_text())
    parsed = app_module.parse_app_config(data)
    assert set(parsed) == {"message", "ios", "android"}
    for name in ("ios", "android"):
        assert parsed[name] == data[name]


def test_route_is_public_and_serves_the_file(client):
    res = client.get("/public/app_config")
    assert res.status_code == 200
    assert res.json() == app_module.parse_app_config(json.loads(CONFIG_PATH.read_text()))


def test_route_serves_both_platforms(client, config_file):
    config_file({"message": "Please update", "ios": platform(), "android": platform(store_url="")})
    body = client.get("/public/app_config").json()
    assert body == {
        "message": "Please update",
        "ios": platform(),
        "android": platform(store_url=""),
    }


@pytest.mark.parametrize("content", ["", "{not json", "[1, 2]", '"text"'])
def test_a_broken_file_means_no_prompt(client, config_file, content):
    config_file(content)
    res = client.get("/public/app_config")
    assert res.status_code == 200
    assert res.json() == {}


def test_a_missing_file_means_no_prompt(client, config_file):
    res = client.get("/public/app_config")
    assert res.status_code == 200
    assert res.json() == {}


@pytest.mark.parametrize(
    "entry",
    [
        platform(latest="3.1"),
        platform(latest="v3.0.3"),
        platform(latest="3.0.x"),
        platform(latest=3),
        {"store_url": "https://example.com/app"},
        platform(store_url="http://example.com/app"),
        platform(store_url=None),
        "3.1.0",
    ],
)
def test_a_bad_platform_is_left_out_and_the_other_kept(client, config_file, entry):
    config_file({"ios": entry, "android": platform()})
    body = client.get("/public/app_config").json()
    assert "ios" not in body
    assert body["android"] == platform()


def test_an_old_min_key_is_ignored(client, config_file):
    """There is no forced update; a `min` left in the file must not break the platform."""
    config_file({"ios": {**platform(), "min": "9.9.9"}})
    assert client.get("/public/app_config").json()["ios"] == platform()


def test_a_missing_message_gets_the_default(client, config_file):
    config_file({"message": "  ", "android": platform()})
    assert client.get("/public/app_config").json()["message"] == app_module.DEFAULT_UPDATE_MESSAGE
