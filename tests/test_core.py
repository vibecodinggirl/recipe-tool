import asyncio
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from downloader import _parse_srt, _validate_url
from extractor import _parse_recipe_json, build_extraction_input
from json_utils import parse_json_object

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clear_runtime_state():
    with main._cache_lock:
        main._cache.clear()
    with main._jobs_lock:
        main._jobs.clear()
    yield


def sample_recipe(**overrides):
    recipe = {
        "title": "Pasta",
        "servings": "2 Portionen",
        "ingredients": ["200 g Nudeln"],
        "steps": ["Nudeln kochen."],
        "tips": "",
        "source_url": "https://www.tiktok.com/@cook/video/123",
    }
    recipe.update(overrides)
    return recipe


@pytest.mark.parametrize(
    "raw",
    [
        '{"answer": 42}',
        'Hier ist es: {"answer": 42} – fertig.',
        '```json\n{"answer": 42}\n```',
    ],
)
def test_parse_json_object_accepts_common_llm_formats(raw):
    assert parse_json_object(raw) == {"answer": 42}


@pytest.mark.parametrize("raw", ["", "kein JSON", "[]", "{kaputt}"])
def test_parse_json_object_rejects_invalid_responses(raw):
    with pytest.raises(ValueError):
        parse_json_object(raw)


def test_recipe_parser_validates_required_fields():
    with pytest.raises(ValueError, match="Fehlendes Feld"):
        _parse_recipe_json('{"title": "Pasta"}')


def test_build_extraction_input_uses_available_sources():
    result = build_extraction_input(caption="Ein vollständiges Rezept mit Zutaten", title="Pasta")
    assert "CAPTION/BESCHREIBUNG" in result
    assert "VIDEO-TITEL" in result


def test_build_extraction_input_rejects_empty_sources():
    with pytest.raises(ValueError, match="Keine verwertbaren Informationen"):
        build_extraction_input(caption="kurz")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/abc/",
        "https://vm.tiktok.com/abc/",
        "https://subdomain.tiktok.com/video/123",
    ],
)
def test_supported_video_urls_are_accepted(url):
    _validate_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://example.com/video", "https://tiktok.com.evil.example/video", "file:///tmp/video"],
)
def test_unsupported_video_urls_are_rejected(url):
    with pytest.raises(ValueError):
        _validate_url(url)


def test_srt_parser_removes_metadata_tags_and_adjacent_duplicates(tmp_path):
    subtitle = tmp_path / "sample.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n<i>Hallo</i>\nHallo\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWelt\n",
        encoding="utf-8",
    )
    assert _parse_srt(str(subtitle)) == "Hallo Welt"


def test_cache_normalizes_urls_and_protects_stored_data():
    recipe = sample_recipe()
    main._cache_set("https://example.com/Recipe/?tracking=1", recipe)
    recipe["title"] = "Mutation außerhalb"

    cached = main._cache_get("https://example.com/recipe")
    assert cached["title"] == "Pasta"
    cached["title"] = "Mutation am Rückgabewert"
    assert main._cache_get("https://example.com/recipe")["title"] == "Pasta"


def test_expired_cache_entry_is_removed(monkeypatch):
    main._cache_set("https://example.com/recipe", sample_recipe())
    monkeypatch.setattr(main, "CACHE_TTL", -1)
    assert main._cache_get("https://example.com/recipe") is None
    assert not main._cache


def test_job_cleanup_removes_expired_jobs(monkeypatch):
    monkeypatch.setattr(main, "JOB_TTL", 1)
    main._jobs["old"] = {"created_at": time.time() - 2}
    main._jobs_cleanup()
    assert "old" not in main._jobs


def test_recipe_card_escapes_model_generated_html():
    rendered = main._generate_recipe_card_html(
        sample_recipe(title="<script>alert(1)</script>", ingredients=["<b>Nudeln</b>"])
    )
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;b&gt;Nudeln&lt;/b&gt;" in rendered


def test_shopping_list_requires_exactly_one_input():
    request = main.ShoppingListRequest()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.shopping_list(request))
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("path", ["/wake", "/cache/stats", "/styles"])
def test_basic_api_endpoints_are_available(path):
    response = client.get(path)
    assert response.status_code == 200


def test_extract_rejects_malformed_url_before_external_work():
    response = client.post("/extract", json={"url": "kein-link"})
    assert response.status_code == 422


def test_shortcut_endpoint_returns_ready_to_save_plain_text(monkeypatch):
    monkeypatch.setattr(main, "_extract_recipe_sync", lambda url: sample_recipe(formatted_note="Fertige Notiz"))
    response = client.get(
        "/shortcut",
        params={"url": "https://www.tiktok.com/@cook/video/123"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "Fertige Notiz"


def test_shortcut_post_accepts_shortcut_friendly_json(monkeypatch):
    monkeypatch.setattr(main, "_extract_recipe_sync", lambda url: sample_recipe(formatted_note="Fertige Notiz"))
    response = client.post(
        "/shortcut",
        json={"url": "https://www.instagram.com/reel/abc/"},
    )
    assert response.status_code == 200
    assert response.text == "Fertige Notiz"


def test_shortcut_endpoint_requires_a_valid_url():
    response = client.get("/shortcut", params={"url": "kein-link"})
    assert response.status_code == 422
