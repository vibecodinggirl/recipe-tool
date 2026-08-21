import asyncio
import ast
import sqlite3
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import storage
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


@pytest.fixture
def temporary_database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "test.db"))
    storage.initialize()
    return tmp_path / "test.db"


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


def test_docker_image_copies_all_local_runtime_modules():
    project_root = Path(__file__).resolve().parents[1]
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    copied_files = set()
    for line in dockerfile.splitlines():
        if line.startswith("COPY ") and line.endswith(" ./"):
            copied_files.update(line.removeprefix("COPY ").removesuffix(" ./").split())

    runtime_files = {"main.py"}
    pending_files = ["main.py"]
    while pending_files:
        filename = pending_files.pop()
        tree = ast.parse((project_root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                candidate = f"{node.module.split('.')[0]}.py"
                if (project_root / candidate).exists() and candidate not in runtime_files:
                    runtime_files.add(candidate)
                    pending_files.append(candidate)

    assert runtime_files <= copied_files, f"Im Docker-Image fehlen: {sorted(runtime_files - copied_files)}"


def test_shopping_filter_removes_available_items_and_preserves_order():
    request = main.ShoppingListFilterRequest(
        items=["200 g Nudeln", "2 Eier", "1 Zitrone"],
        available=["2 Eier"],
    )
    result = asyncio.run(main.shopping_list_filter(request))
    assert result == {
        "items": ["200 g Nudeln", "1 Zitrone"],
        "items_text": "200 g Nudeln\n1 Zitrone",
        "removed_count": 1,
    }


def test_shopping_filter_handles_no_available_items():
    request = main.ShoppingListFilterRequest(items=["Milch"], available=[])
    assert asyncio.run(main.shopping_list_filter(request))["items"] == ["Milch"]


def test_shopping_filter_requires_candidate_items():
    with pytest.raises(ValueError):
        main.ShoppingListFilterRequest(items=[])


def test_grocery_start_returns_only_job_id(monkeypatch):
    monkeypatch.setattr(main, "_extract_recipe_sync", lambda url: sample_recipe(formatted_note="Notiz"))
    response = client.post(
        "/grocery-start",
        json={"url": "https://www.tiktok.com/@cook/video/123"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert len(response.text) == 32


def test_grocery_wait_returns_ingredients_without_dictionary_steps():
    main._jobs["ready"] = {
        "status": "done",
        "result": sample_recipe(),
        "error": None,
        "created_at": time.time(),
    }
    response = client.get("/grocery-wait/ready")
    assert response.status_code == 200
    assert response.json() == ["200 g Nudeln"]


def test_grocery_wait_reports_missing_job():
    response = client.get("/grocery-wait/unknown")
    assert response.status_code == 404


def test_normalize_grocery_items_cleans_and_deduplicates():
    result = main._normalize_grocery_items(
        ["  • 2 Eier  ", "2  Eier", "- 500 g Mehl", None, ""]
    )
    assert result == ["2 Eier", "500 g Mehl"]


def test_normalize_grocery_items_requires_a_list():
    with pytest.raises(ValueError, match="Zutatenliste"):
        main._normalize_grocery_items("2 Eier")


def test_smart_grocery_list_combines_recipes_and_scaling(monkeypatch):
    captured = {}

    def fake_llm_query(prompt, user_input):
        captured["prompt"] = prompt
        captured["user_input"] = user_input
        return '{"items": ["5 Eier", "500 g Mehl", "5  Eier"]}'

    monkeypatch.setattr(main, "llm_query", fake_llm_query)
    response = client.post(
        "/smart-grocery-list",
        json={
            "recipes": ["Rezept A: 2 Eier", "Rezept B: 3 Eier und Mehl"],
            "target_servings": 4,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "items": ["5 Eier", "500 g Mehl"],
        "items_text": "5 Eier\n500 g Mehl",
        "recipe_count": 2,
        "target_servings": 4,
    }
    assert "4 Portionen" in captured["prompt"]
    assert "REZEPT 1" in captured["user_input"]
    assert "REZEPT 2" in captured["user_input"]


def test_smart_grocery_list_rejects_empty_recipe_text():
    response = client.post("/smart-grocery-list", json={"recipes": ["   "]})
    assert response.status_code == 400


def test_smart_grocery_list_limits_recipe_count():
    response = client.post("/smart-grocery-list", json={"recipes": ["Rezept"] * 11})
    assert response.status_code == 422


def test_normalize_grocery_updates_uses_exact_existing_titles():
    result = main._normalize_grocery_updates(
        [
            {"existing": "2 eier", "replacement": "5 Eier"},
            {"existing": "Nicht vorhanden", "replacement": "1 kg Reis"},
            {"existing": "2 Eier", "replacement": "6 Eier"},
            "ungültig",
        ],
        ["2 Eier", "Milch"],
    )
    assert result == [{"existing": "2 Eier", "replacement": "5 Eier"}]


def test_merge_grocery_list_returns_add_and_update_operations(monkeypatch):
    captured = {}

    def fake_llm_query(prompt, user_input):
        captured["user_input"] = user_input
        return """{
            "add": ["1 Zitrone", "  1  Zitrone "],
            "update": [
                {"existing": "2 eier", "replacement": "5 Eier"},
                {"existing": "Erfundener Eintrag", "replacement": "1 kg Reis"}
            ]
        }"""

    monkeypatch.setattr(main, "llm_query", fake_llm_query)
    response = client.post(
        "/merge-grocery-list",
        json={
            "recipe_text": "Für den Kuchen: 3 Eier und eine Zitrone",
            "existing_items": ["2 Eier", "Milch"],
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "add": ["1 Zitrone"],
        "update": [{"existing": "2 Eier", "replacement": "5 Eier"}],
    }
    assert "2 Eier" in captured["user_input"]
    assert "3 Eier" in captured["user_input"]


def test_merge_grocery_list_rejects_empty_recipe():
    response = client.post(
        "/merge-grocery-list",
        json={"recipe_text": "", "existing_items": []},
    )
    assert response.status_code == 422


def test_recipe_plan_and_tracker_share_persistent_data(temporary_database):
    recipe = storage.save_recipe(
        sample_recipe(),
        {"per_serving": {"calories": 500, "protein_g": 20, "carbs_g": 60, "fat_g": 15}},
    )
    assert storage.get_recipe(recipe["id"])["ingredients"] == ["200 g Nudeln"]
    storage.add_plan("2026-08-21", "Abendessen", recipe["id"], 1.5)
    assert storage.list_plan("2026-08-20", "2026-08-26")[0]["title"] == "Pasta"

    storage.set_calorie_target(2200)
    storage.log_food("Pasta", 1.5, {"calories": 500, "protein_g": 20, "carbs_g": 60, "fat_g": 15},
                     recipe["id"], "2026-08-21T18:00:00")
    summary = storage.daily_summary("2026-08-21")
    assert summary["totals"]["calories"] == 750
    assert summary["remaining_calories"] == 1450


def test_tracker_rejects_recipe_without_nutrition(temporary_database):
    recipe = storage.save_recipe(sample_recipe())
    response = client.post("/tracker/log", json={"recipe_id": recipe["id"], "servings": 1})
    assert response.status_code == 400


def test_recipe_library_avoids_duplicate_source_urls(temporary_database):
    first = storage.save_recipe(sample_recipe())
    second = storage.save_recipe(sample_recipe(title="Dasselbe Video"))
    assert second["id"] == first["id"]
    assert len(storage.list_recipes()) == 1


def test_dashboard_combines_library_plan_and_tracker(temporary_database):
    recipe = storage.save_recipe(
        sample_recipe(),
        {"per_serving": {"calories": 500, "protein_g": 20, "carbs_g": 60, "fat_g": 15}},
    )
    storage.add_plan(date.today().isoformat(), "Abendessen", recipe["id"], 1)
    storage.log_food("Pasta", 1, {"calories": 500, "protein_g": 20, "carbs_g": 60, "fat_g": 15})
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Meine Rezepte" in response.text
    assert "Heute gegessen" in response.text
    assert "Abendessen" in response.text


def test_extended_health_targets_and_nutrients(temporary_database):
    recipe = storage.save_recipe(sample_recipe(), {"per_serving": {
        "calories": 500, "protein_g": 22, "carbs_g": 60, "fat_g": 15,
        "fiber_g": 9, "sugar_g": 7, "salt_g": 1.1,
    }})
    storage.set_health_targets({
        "calories": 2100, "protein_g": 120, "carbs_g": 230, "fat_g": 70,
        "fiber_g": 35, "sugar_g": 45, "salt_g": 5,
    })
    storage.log_food("Pasta", 2, {key: recipe[key] for key in storage.NUTRIENT_FIELDS})
    summary = storage.daily_summary(date.today().isoformat())
    assert summary["totals"]["fiber_g"] == 18
    assert summary["totals"]["sugar_g"] == 14
    assert summary["targets"]["protein_g"] == 120
    assert summary["targets"]["salt_g"] == 5


def test_weight_history_and_goal(temporary_database):
    storage.set_weight_goal(65)
    storage.log_weight(68.4, "2026-08-21T08:00:00")
    result = storage.weight_history()
    assert result["goal_weight_kg"] == 65
    assert result["entries"][0]["weight_kg"] == 68.4


def test_plan_assessment_warns_about_missing_healthy_components():
    assessment = storage.assess_plan([{
        "title": "Nudeln", "servings": 1, "ingredients": '["Nudeln", "Sahne"]',
        "protein_g": 8, "fiber_g": 2,
    }])
    assert assessment["status"] == "warning"
    assert any("Protein" in warning for warning in assessment["warnings"])
    assert any("Gemüse" in warning for warning in assessment["warnings"])


def test_health_dashboard_sections_are_visible(temporary_database):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Ballaststoffe" in response.text
    assert "Letzte 7 Tage" in response.text
    assert "Zielgewicht" in response.text


def test_initialize_migrates_existing_database_without_losing_recipes(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE recipes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            servings TEXT NOT NULL DEFAULT '', ingredients TEXT NOT NULL, steps TEXT NOT NULL,
            tips TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '', calories REAL,
            protein_g REAL, carbs_g REAL, fat_g REAL, created_at TEXT NOT NULL)""")
        db.execute("""INSERT INTO recipes(title,ingredients,steps,created_at) VALUES('Alt','[]','[]','2026-08-21')""")
    monkeypatch.setattr(storage, "DB_PATH", str(db_path))
    storage.initialize()
    recipe = storage.list_recipes()[0]
    assert recipe["title"] == "Alt"
    assert "fiber_g" in recipe
