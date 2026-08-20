"""
Recipe Tool - Extrahiert Rezepte aus Instagram Reels & TikTok Videos
und speichert sie in Apple Notizen via Apple Shortcut.

Diese Version enthält zusätzlich Async-Job-Endpunkte für Apple Kurzbefehle:
- POST /extract-start        -> startet Extraktion und gibt sofort job_id zurück
- GET  /extract-result/{id}  -> fragt Ergebnis ab

Der alte Endpoint bleibt erhalten:
- POST /extract              -> blockierende Extraktion wie bisher
"""

from __future__ import annotations

import os
import asyncio
import copy
import hashlib
import html
import logging
import time
import uuid
import threading
from datetime import date, timedelta
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, HttpUrl, Field
from starlette.concurrency import run_in_threadpool
from dotenv import load_dotenv

load_dotenv()

from downloader import download_video_data, _download_audio
from extractor import (
    transcribe_audio,
    extract_recipe,
    build_extraction_input,
    check_local_dependencies,
    MODE,
    llm_query,
)
from ocr import extract_text_from_frames, cleanup_frames
from json_utils import parse_json_object
import storage

NOTE_STYLE = os.getenv("NOTE_STYLE", "classic")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# In-Memory Cache (max 100 Rezepte, 24h TTL)
# ============================================================

MAX_CACHE = int(os.getenv("MAX_CACHE", "100"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "86400"))  # 24 Stunden
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(url: str) -> str:
    """Normalisiert URL und gibt einen Cache-Key zurück."""
    clean = url.split("?")[0].rstrip("/").lower()
    return hashlib.md5(clean.encode()).hexdigest()


def _cache_get(url: str) -> dict | None:
    key = _cache_key(url)
    with _cache_lock:
        if key in _cache:
            ts, data = _cache[key]
            if time.time() - ts < CACHE_TTL:
                logger.info(f"Cache HIT für {url[:60]}")
                _cache.move_to_end(key)
                return copy.deepcopy(data)
            del _cache[key]
    return None


def _cache_set(url: str, data: dict):
    key = _cache_key(url)
    with _cache_lock:
        _cache[key] = (time.time(), copy.deepcopy(data))
        _cache.move_to_end(key)
        while len(_cache) > MAX_CACHE:
            _cache.popitem(last=False)


# ============================================================
# In-Memory Jobs für lange Video-Extraktion
# ============================================================

MAX_JOBS = int(os.getenv("MAX_JOBS", "100"))
JOB_TTL = int(os.getenv("JOB_TTL", "3600"))  # 1 Stunde
_jobs: OrderedDict[str, dict] = OrderedDict()
_jobs_lock = threading.Lock()


def _jobs_cleanup():
    """Entfernt alte Jobs, damit der Speicher nicht wächst."""
    with _jobs_lock:
        now = time.time()
        expired = [job_id for job_id, job in _jobs.items() if now - job.get("created_at", now) > JOB_TTL]
        for job_id in expired:
            _jobs.pop(job_id, None)

        while len(_jobs) > MAX_JOBS:
            _jobs.popitem(last=False)


def _job_public(job: dict) -> dict:
    """Entfernt interne Felder aus Job-Antworten."""
    return {
        "status": job.get("status"),
        "result": job.get("result"),
        "error": job.get("error"),
    }


# ============================================================
# Keep-Alive (pingt sich selbst alle 10 Min → kein Cold Start)
# ============================================================

KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", "600"))


async def _keep_alive_loop():
    """Pingt den eigenen /wake Endpoint um Render-Sleep zu verhindern."""
    import httpx

    render_url = os.getenv("RENDER_EXTERNAL_URL", "")
    if not render_url:
        logger.info("RENDER_EXTERNAL_URL nicht gesetzt — Keep-Alive deaktiviert")
        return

    wake_url = f"{render_url}/wake"
    logger.info(f"Keep-Alive aktiv: Pinge {wake_url} alle {KEEP_ALIVE_INTERVAL}s")

    while True:
        await asyncio.sleep(KEEP_ALIVE_INTERVAL)
        try:
            async with httpx.AsyncClient() as client:
                await client.get(wake_url, timeout=10.0)
            logger.debug("Keep-Alive ping OK")
        except Exception as e:
            logger.warning(f"Keep-Alive ping fehlgeschlagen: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("tmp", exist_ok=True)
    storage.initialize()
    keep_alive_task = asyncio.create_task(_keep_alive_loop())
    yield
    keep_alive_task.cancel()

    for f in os.listdir("tmp"):
        try:
            os.remove(os.path.join("tmp", f))
        except OSError:
            pass


app = FastAPI(
    title="Recipe Extractor",
    description="Extrahiert Rezepte aus Instagram Reels & TikTok Videos",
    version="1.4.0",
    lifespan=lifespan,
)

MAX_REQUEST_TEXT_LEN = int(os.getenv("MAX_REQUEST_TEXT_LEN", "5000"))


# ============================================================
# Request / Response Models
# ============================================================

class VideoRequest(BaseModel):
    url: HttpUrl


class ScaleRequest(BaseModel):
    title: str
    servings: str
    ingredients: list[str]
    steps: list[str]
    target_servings: str


class ShoppingListRequest(BaseModel):
    ingredients: list[str] = Field(default_factory=list)
    title: str = ""
    text: str = ""


class ShoppingListFilterRequest(BaseModel):
    items: list[str] = Field(min_length=1)
    available: list[str] = Field(default_factory=list)


class SmartGroceryRequest(BaseModel):
    recipes: list[str] = Field(min_length=1, max_length=10)
    target_servings: Optional[int] = Field(default=None, ge=1, le=50)


class GroceryMergeRequest(BaseModel):
    recipe_text: str = Field(min_length=1, max_length=MAX_REQUEST_TEXT_LEN)
    existing_items: list[str] = Field(default_factory=list, max_length=200)


class NutritionRequest(BaseModel):
    title: str
    ingredients: list[str]
    servings: str = ""


class MealPlanRequest(BaseModel):
    days: int = Field(default=5, ge=1, le=7)
    preferences: str = ""


class SavedRecipeRequest(BaseModel):
    title: str
    servings: str = ""
    ingredients: list[str]
    steps: list[str] = Field(default_factory=list)
    tips: str = ""
    source_url: str = ""
    nutrition: dict = Field(default_factory=dict)


class PlanEntryRequest(BaseModel):
    plan_date: str
    meal_type: str
    recipe_id: int
    servings: float = Field(default=1, gt=0, le=20)


class FoodLogRequest(BaseModel):
    recipe_id: int
    servings: float = Field(default=1, gt=0, le=20)
    eaten_at: str = ""


class CalorieTargetRequest(BaseModel):
    calories: float = Field(gt=0, le=10000)


class RecipeResponse(BaseModel):
    title: str
    servings: str
    ingredients: list[str]
    steps: list[str]
    tips: str
    source_url: str
    formatted_note: str


# ============================================================
# Basic Endpoints
# ============================================================

@app.get("/health")
async def health():
    info = {"status": "ok", "mode": MODE}
    if MODE == "local":
        info["dependencies"] = check_local_dependencies()
    return info


@app.get("/wake")
async def wake():
    """Leichtgewichtiger Endpoint zum Aufwecken des Servers."""
    return {"status": "awake"}


@app.get("/shortcut", response_class=PlainTextResponse)
async def shortcut_recipe(url: HttpUrl):
    """Ein-Aufruf-Schnittstelle für Apple Kurzbefehle: URL rein, fertige Notiz raus."""
    recipe = await run_in_threadpool(_extract_recipe_sync, str(url))
    return recipe["formatted_note"]


@app.post("/shortcut", response_class=PlainTextResponse)
async def shortcut_recipe_post(request: VideoRequest):
    """POST-Variante ohne URL-Encoding-Probleme in Apple Kurzbefehlen."""
    return await shortcut_recipe(request.url)


@app.get("/cache/stats")
async def cache_stats():
    """Zeigt Cache-Statistiken."""
    return {
        "cached_recipes": len(_cache),
        "max_cache": MAX_CACHE,
        "ttl_seconds": CACHE_TTL,
        "jobs": len(_jobs),
        "max_jobs": MAX_JOBS,
        "job_ttl_seconds": JOB_TTL,
    }


@app.exception_handler(HTTPException)
async def friendly_error_handler(request: Request, exc: HTTPException):
    """Gibt Shortcut-freundliche Fehlermeldungen zurück."""
    error_messages = {
        400: "❌ Link ungültig oder Video nicht erreichbar. Versuche einen anderen Link.",
        422: "❌ Kein Rezept gefunden. Das Video hat wohl kein Rezept in der Beschreibung.",
        500: "❌ Server-Fehler. Bitte versuche es in ein paar Minuten nochmal.",
    }
    friendly = error_messages.get(exc.status_code, f"❌ Fehler: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error_message": friendly},
    )


# ============================================================
# Debug Endpoint
# ============================================================

@app.post("/debug")
async def debug_endpoint(request: VideoRequest):
    """Debug: zeigt was bei jedem Schritt passiert."""
    url = str(request.url)
    result = {"url": url, "steps": {}}

    try:
        video_data = download_video_data(url, fast_mode=True)
        result["steps"]["download"] = "ok"
        result["caption_len"] = len(video_data.get("caption", ""))
        result["title"] = video_data.get("title", "")[:100]
        result["caption_preview"] = video_data.get("caption", "")[:300]
        result["subtitles_len"] = len(video_data.get("subtitles", ""))
        result["subtitles_preview"] = video_data.get("subtitles", "")[:200]
        result["has_audio"] = video_data.get("audio_path") is not None
        result["has_frames"] = video_data.get("frames_dir") is not None
    except Exception as e:
        result["steps"]["download"] = f"FEHLER: {str(e)}"

    import httpx
    from urllib.parse import quote
    from downloader import _resolve_short_url

    try:
        resolved_url = _resolve_short_url(url)
        result["resolved_url"] = resolved_url
        if "tiktok.com" in resolved_url:
            encoded = quote(resolved_url, safe="")
            oembed_url = f"https://www.tiktok.com/oembed?url={encoded}"
        else:
            encoded = quote(resolved_url, safe="")
            oembed_url = f"https://graph.facebook.com/v18.0/instagram_oembed?url={encoded}&omitscript=true"

        r = httpx.get(
            oembed_url,
            follow_redirects=True,
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        result["oembed_status"] = r.status_code
        result["oembed_url"] = oembed_url

        if r.status_code == 200:
            try:
                data = r.json()
                result["oembed_title"] = data.get("title", "")[:300]
                result["oembed_author"] = data.get("author_name", "")
                result["oembed_html"] = data.get("html", "")[:200]
            except Exception:
                result["oembed_body"] = r.text[:300]
        else:
            result["oembed_body"] = r.text[:300]
    except Exception as e:
        result["oembed_error"] = str(e)

    return result


# ============================================================
# Rezept-Extraktion: neue Async-Job-Endpunkte
# ============================================================

@app.post("/extract-start")
async def extract_start(request: VideoRequest, background_tasks: BackgroundTasks):
    """
    Startet die lange Rezept-Extraktion im Hintergrund.
    Dieser Endpoint antwortet sofort, damit Apple Kurzbefehle nicht timeoutet.
    """
    _jobs_cleanup()

    url = str(request.url)
    cached = _cache_get(url)
    job_id = uuid.uuid4().hex

    if cached:
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "done",
                "result": cached,
                "error": None,
                "created_at": time.time(),
                "url": url,
            }
        return {"job_id": job_id, "status": "done"}

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "url": url,
        }

    background_tasks.add_task(_run_extract_job, job_id, url)
    return {"job_id": job_id, "status": "running"}


@app.get("/extract-result/{job_id}")
async def extract_result(job_id: str):
    """Fragt Status oder Ergebnis eines laufenden Extract-Jobs ab."""
    _jobs_cleanup()

    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return {
            "status": "not_found",
            "result": None,
            "error": "Job nicht gefunden. Eventuell wurde der Server neu gestartet oder der Job ist abgelaufen.",
        }

    return _job_public(job)


@app.get("/extract-wait/{job_id}")
async def extract_wait(job_id: str):
    """Wartet bis zu 55 Sekunden auf das Ergebnis. Kein Polling nötig."""
    _jobs_cleanup()

    for _ in range(55):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job:
            return {
                "status": "not_found",
                "result": None,
                "error": "Job nicht gefunden.",
            }
        if job.get("status") in ("done", "error"):
            return _job_public(job)
        await asyncio.sleep(1)

    return _job_public(job)


@app.post("/grocery-start", response_class=PlainTextResponse)
async def grocery_start(request: VideoRequest, background_tasks: BackgroundTasks):
    """Startet die Extraktion und gibt für Kurzbefehle nur die Job-ID zurück."""
    job = await extract_start(request, background_tasks)
    return job["job_id"]


@app.get("/grocery-wait/{job_id}")
async def grocery_wait(job_id: str):
    """Wartet auf die Extraktion und gibt direkt die benötigten Zutaten zurück."""
    job = await extract_wait(job_id)
    status = job.get("status")
    if status == "done":
        return job["result"].get("ingredients", [])
    if status == "error":
        raise HTTPException(status_code=500, detail=job.get("error") or "Extraktion fehlgeschlagen.")
    if status == "not_found":
        raise HTTPException(status_code=404, detail=job.get("error") or "Job nicht gefunden.")
    raise HTTPException(status_code=202, detail="Das Rezept wird noch verarbeitet. Bitte erneut versuchen.")


def _run_extract_job(job_id: str, url: str):
    """Läuft im Hintergrund und speichert Ergebnis oder Fehler in _jobs."""
    try:
        recipe = _extract_recipe_sync(url)
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "done",
                "result": recipe,
                "error": None,
                "created_at": _jobs.get(job_id, {}).get("created_at", time.time()),
                "url": url,
            }
    except Exception as e:
        logger.exception(f"Extract job failed: {e}")
        error_detail = str(e)
        if isinstance(e, HTTPException):
            error_detail = e.detail
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "error",
                "result": None,
                "error": error_detail,
                "created_at": _jobs.get(job_id, {}).get("created_at", time.time()),
                "url": url,
            }


# ============================================================
# Rezept-Extraktion: alte blockierende Route bleibt erhalten
# ============================================================

@app.post("/extract", response_model=RecipeResponse)
async def extract_recipe_endpoint(request: VideoRequest):
    """Alte blockierende Extraktion. Für Apple Shortcut besser /extract-start nutzen."""
    recipe = await run_in_threadpool(_extract_recipe_sync, str(request.url))
    return RecipeResponse(**recipe)


def _extract_recipe_sync(url: str) -> dict:
    """
    Eigentliche Rezept-Extraktion als normale Funktion.
    Wird von /extract und vom Hintergrundjob verwendet.
    """
    logger.info(f"Processing URL: {url}")

    cached = _cache_get(url)
    if cached:
        return cached

    try:
        video_data = download_video_data(url, fast_mode=True)
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise HTTPException(status_code=400, detail=f"Video konnte nicht geladen werden: {str(e)}")

    caption = video_data.get("caption", "")
    title = video_data.get("title", "")
    subtitles = video_data.get("subtitles", "")
    audio_path = video_data.get("audio_path")
    frames_dir = video_data.get("frames_dir")

    total_text = len(caption) + len(subtitles)
    generic_titles = ("TikTok - Make Your Day", "TikTok", "")
    title_useful = title not in generic_titles
    has_enough_caption = total_text >= 50 or title_useful

    logger.info(
        f"Pass 1: Caption={len(caption)}z, Subs={len(subtitles)}z, "
        f"Title='{title[:50]}', genug={has_enough_caption}"
    )

    if not has_enough_caption:
        logger.info("Caption zu kurz — versuche Audio-Download + Transkription...")
        file_id = uuid.uuid4().hex[:12]
        try:
            audio_path = _download_audio(url, file_id)
        except Exception as e:
            logger.warning(f"Audio-Download fehlgeschlagen: {e}")

    transcript = ""
    ocr_text = ""
    try:
        if subtitles:
            logger.info(f"Nutze Untertitel als Transkript ({len(subtitles)} Zeichen)")
            transcript = subtitles
        elif audio_path:
            try:
                transcript = transcribe_audio(audio_path)
            except Exception as e:
                logger.warning(f"Transkription fehlgeschlagen: {e}")

        if frames_dir:
            try:
                ocr_text = extract_text_from_frames(frames_dir)
            except Exception as e:
                logger.warning(f"OCR fehlgeschlagen: {e}")

        logger.info(
            f"Quellen: Audio={len(transcript)}z, Caption={len(caption)}z, "
            f"OCR={len(ocr_text)}z, Titel={len(title)}z"
        )

        total_content = len(transcript) + len(caption) + len(ocr_text)
        if total_content < 50 and not title_useful:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Konnte kein Rezept finden — weder in der Beschreibung noch im Audio. "
                    "Funktioniert am besten bei Videos wo das Rezept im Text steht."
                ),
            )

        try:
            combined_input = build_extraction_input(
                transcript=transcript,
                caption=caption,
                ocr_text=ocr_text,
                title=title,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        logger.info("Extracting recipe...")
        try:
            recipe = extract_recipe(combined_input, url)
        except Exception as e:
            logger.error(f"Recipe extraction failed: {e}")
            raise HTTPException(status_code=500, detail=f"Rezept-Extraktion fehlgeschlagen: {str(e)}")

        formatted = format_for_apple_notes(recipe)
        recipe["formatted_note"] = formatted

        _cache_set(url, recipe)
        try:
            storage.save_recipe(recipe)
        except Exception as e:
            logger.warning(f"Rezept konnte nicht dauerhaft gespeichert werden: {e}")
        return recipe

    finally:
        # Cleanup temp files — safe because this runs AFTER transcription completes
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass
        cleanup_frames(frames_dir)


# ============================================================
# Notiz-Formatierung – Styles
# ============================================================

def format_for_apple_notes(recipe: dict) -> str:
    """Formatiert das Rezept je nach gewähltem Style."""
    formatters = {
        "classic": _format_classic,
        "minimal": _format_minimal,
        "card": _format_card,
        "checklist": _format_checklist,
    }
    formatter = formatters.get(NOTE_STYLE, _format_classic)
    return formatter(recipe)


def _format_classic(r: dict) -> str:
    """Klassisch mit Emojis – übersichtlich und freundlich."""
    lines = [
        f"🍳 {r['title']}",
        f"👥 {r['servings']}",
        "",
        "📝 Zutaten:",
    ]
    for ing in r["ingredients"]:
        lines.append(f"  • {ing}")
    lines.append("")
    lines.append("👨\u200d🍳 Zubereitung:")
    for i, step in enumerate(r["steps"], 1):
        lines.append(f"  {i}. {step}")
    if r.get("tips"):
        lines += ["", f"💡 Tipps: {r['tips']}"]
    lines += ["", f"📱 Quelle: {r['source_url']}"]
    return "\n".join(lines)


def _format_minimal(r: dict) -> str:
    """Minimalistisch – nur das Wesentliche, kein Schnickschnack."""
    lines = [
        r["title"].upper(),
        r["servings"],
        "",
        "ZUTATEN",
    ]
    for ing in r["ingredients"]:
        lines.append(f"- {ing}")
    lines.append("")
    lines.append("ZUBEREITUNG")
    for i, step in enumerate(r["steps"], 1):
        lines.append(f"{i}. {step}")
    if r.get("tips"):
        lines += ["", f"Tipp: {r['tips']}"]
    lines += ["", r["source_url"]]
    return "\n".join(lines)


def _format_card(r: dict) -> str:
    """Rezeptkarte – hübsch mit Rahmen und Abtrennung."""
    w = 40
    sep = "─" * w
    lines = [
        f"┌{sep}┐",
        f"  🍽️  {r['title']}",
        f"  {r['servings']}",
        f"├{sep}┤",
        "  Zutaten:",
    ]
    for ing in r["ingredients"]:
        lines.append(f"    ◦ {ing}")
    lines.append(f"├{sep}┤")
    lines.append("  Zubereitung:")
    for i, step in enumerate(r["steps"], 1):
        lines.append(f"    {i}. {step}")
    if r.get("tips"):
        lines.append(f"├{sep}┤")
        lines.append(f"  💡 {r['tips']}")
    lines.append(f"├{sep}┤")
    lines.append(f"  🔗 {r['source_url']}")
    lines.append(f"└{sep}┘")
    return "\n".join(lines)


def _format_checklist(r: dict) -> str:
    """Checklisten-Format – zum Abhaken beim Kochen."""
    lines = [
        f"🍳 {r['title']}  ({r['servings']})",
        "",
        "▸ EINKAUFSLISTE:",
    ]
    for ing in r["ingredients"]:
        lines.append(f"  ☐ {ing}")
    lines.append("")
    lines.append("▸ SCHRITTE:")
    for i, step in enumerate(r["steps"], 1):
        lines.append(f"  ☐ {i}. {step}")
    if r.get("tips"):
        lines += ["", f"💡 {r['tips']}"]
    lines += ["", f"📱 {r['source_url']}"]
    return "\n".join(lines)


@app.get("/styles")
async def list_styles():
    """Zeigt alle verfügbaren Notiz-Styles mit Beispiel."""
    example = {
        "title": "Pasta Aglio e Olio",
        "servings": "2 Portionen",
        "ingredients": ["250g Spaghetti", "4 Knoblauchzehen", "Olivenöl", "Chiliflocken"],
        "steps": ["Pasta kochen.", "Knoblauch in Öl anbraten.", "Alles vermengen."],
        "tips": "Frische Petersilie dazu!",
        "source_url": "https://example.com/reel/123",
    }
    return {
        "current": NOTE_STYLE,
        "available": {
            "classic": {"description": "Mit Emojis, übersichtlich", "preview": _format_classic(example)},
            "minimal": {"description": "Nur Text, kein Schnickschnack", "preview": _format_minimal(example)},
            "card": {"description": "Rezeptkarte mit Rahmen", "preview": _format_card(example)},
            "checklist": {"description": "Zum Abhaken beim Kochen & Einkaufen", "preview": _format_checklist(example)},
        },
    }


# ============================================================
# Feature: Einkaufsliste
# ============================================================

@app.post("/shopping-list")
async def shopping_list(request: ShoppingListRequest):
    """Erstellt eine kategorisierte Einkaufsliste. Akzeptiert entweder eine Zutaten-Liste ODER Rohtext aus einer Notiz."""
    prompt = """Du organisierst Zutaten als Einkaufsliste. Gruppiere nach Supermarkt-Abteilung.
Falls du Rohtext bekommst (z.B. aus einer Rezept-Notiz kopiert), extrahiere zuerst die Zutaten daraus.
Antworte NUR mit JSON:
{
    "title": "Einkaufsliste für ...",
    "categories": [
        {"name": "🥩 Fleisch & Fisch", "items": ["..."]},
        {"name": "🥬 Obst & Gemüse", "items": ["..."]},
        {"name": "🧀 Milchprodukte", "items": ["..."]},
        {"name": "🍝 Trockenwaren & Gewürze", "items": ["..."]},
        {"name": "🛒 Sonstiges", "items": ["..."]}
    ]
}
Leere Kategorien weglassen. Sprache: Deutsch."""

    if request.text and request.ingredients:
        raise HTTPException(
            status_code=400,
            detail="Bitte entweder 'ingredients' (Liste) ODER 'text' (Rohtext) angeben, nicht beides.",
        )

    if request.text:
        text = request.text[:MAX_REQUEST_TEXT_LEN]
        user_input = f"Erstelle eine Einkaufsliste aus folgendem Rezept-Text:\n\n{text}"
    elif request.ingredients:
        ingredients_text = "\n".join(f"- {ing}" for ing in request.ingredients)
        user_input = f"Rezept: {request.title}\n\nZutaten:\n{ingredients_text}"
    else:
        raise HTTPException(
            status_code=400,
            detail="Entweder 'ingredients' (Liste) oder 'text' (Rohtext) muss angegeben werden.",
        )

    try:
        raw = await run_in_threadpool(llm_query, prompt, user_input)
        data = _parse_json_response(raw)

        lines = [f"🛒 {data.get('title', 'Einkaufsliste')}"]
        for cat in data.get("categories", []):
            lines.append(f"\n{cat['name']}:")
            for item in cat["items"]:
                lines.append(f"  ☐ {item}")
        data["formatted_list"] = "\n".join(lines)

        all_items = []
        reminder_items = []
        for cat in data.get("categories", []):
            cat_name = cat["name"]
            for item in cat["items"]:
                all_items.append(f"{cat_name} {item}")
                reminder_items.append(item)
        data["items"] = all_items
        data["items_text"] = "\n".join(all_items)
        data["reminder_items"] = reminder_items

        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Einkaufsliste fehlgeschlagen: {e}")


@app.post("/shopping-list-simple")
async def shopping_list_simple(request: ShoppingListRequest):
    """Wie /shopping-list, aber gibt NUR eine flache Text-Liste zurück — optimiert für Apple Kurzbefehle."""
    result = await shopping_list(request)
    if isinstance(result, dict):
        return result.get("items", [])
    return []


@app.post("/shopping-list-reminders")
async def shopping_list_reminders(request: ShoppingListRequest):
    """Gibt saubere Einträge für Apples automatisch kategorisierte Einkaufsliste zurück."""
    result = await shopping_list(request)
    return result.get("reminder_items", []) if isinstance(result, dict) else []


@app.post("/shopping-list-filter")
async def shopping_list_filter(request: ShoppingListFilterRequest):
    """Entfernt die vom Nutzer als vorhanden markierten Zutaten aus der Einkaufsliste."""
    available = set(request.available)
    remaining = [item for item in request.items if item not in available]
    return {
        "items": remaining,
        "items_text": "\n".join(remaining),
        "removed_count": len(request.items) - len(remaining),
    }


@app.post("/smart-grocery-list")
async def smart_grocery_list(request: SmartGroceryRequest):
    """Kombiniert, bereinigt und skaliert Zutaten aus bis zu zehn Rezept-Notizen."""
    recipes = [text.strip() for text in request.recipes if text.strip()]
    if not recipes:
        raise HTTPException(status_code=400, detail="Mindestens eine Rezept-Notiz muss Text enthalten.")

    combined_text = "\n\n".join(
        f"=== REZEPT {index} ===\n{text[:MAX_REQUEST_TEXT_LEN]}"
        for index, text in enumerate(recipes, 1)
    )
    combined_text = combined_text[: MAX_REQUEST_TEXT_LEN * 3]
    scaling_rule = (
        f"Skaliere jedes Rezept zuerst auf {request.target_servings} Portionen."
        if request.target_servings
        else "Behalte die in den Rezepten angegebenen Portionen und Mengen bei."
    )
    prompt = f"""Du erstellst eine gemeinsame Einkaufsliste aus einer oder mehreren Rezept-Notizen.

Aufgaben:
1. Extrahiere ausschließlich echte Zutaten, keine Überschriften, Tipps oder Zubereitungsschritte.
2. {scaling_rule}
3. Vereinheitliche gleichbedeutende Schreibweisen und Einheiten.
4. Führe doppelte Zutaten zusammen und addiere kompatible Mengen.
5. Vermische unvereinbare Einheiten nicht; behalte sie dann als getrennte Einträge.
6. Behalte notwendige Mengen und kurze Spezifikationen bei.

Antworte NUR mit JSON:
{{"items": ["Menge Zutat", "Menge Zutat"]}}

Sprache: Deutsch. Keine Kategorien oder Emojis."""

    try:
        raw = await run_in_threadpool(llm_query, prompt, combined_text)
        data = _parse_json_response(raw)
        items = _normalize_grocery_items(data.get("items", []))
        if not items:
            raise ValueError("Keine Zutaten erkannt.")
        return {
            "items": items,
            "items_text": "\n".join(items),
            "recipe_count": len(recipes),
            "target_servings": request.target_servings,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Einkaufsliste konnte nicht erstellt werden: {exc}")


def _normalize_grocery_items(items: object) -> list[str]:
    """Bereinigt Modell-Ausgaben und entfernt exakte Duplikate ohne Reihenfolgeverlust."""
    if not isinstance(items, list):
        raise ValueError("Die KI-Antwort enthält keine Zutatenliste.")

    normalized = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        clean = " ".join(item.strip().lstrip("-•☐ ").split())
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            normalized.append(clean)
    return normalized


@app.post("/merge-grocery-list")
async def merge_grocery_list(request: GroceryMergeRequest):
    """Vergleicht ein Rezept mit Apple Erinnerungen und addiert passende Mengen."""
    existing_items = _normalize_grocery_items(request.existing_items)
    existing_text = "\n".join(f"- {item}" for item in existing_items) or "(leer)"
    prompt = """Du vergleichst eine Rezept-Notiz mit einer bestehenden Einkaufsliste.

Regeln:
1. Extrahiere ausschließlich Zutaten aus der Rezept-Notiz.
2. Erkenne gleiche Zutaten trotz Singular/Plural, Reihenfolge und unterschiedlicher Mengenangaben.
3. Addiere kompatible Mengen und rechne Einheiten um, z.B. 500 g + 1 kg = 1,5 kg.
4. Ändere einen bestehenden Eintrag nur, wenn die Zutat eindeutig dieselbe ist.
5. Bei unvereinbaren oder unklaren Einheiten füge einen neuen Eintrag hinzu.
6. Bereits ausreichende Einträge ohne sinnvoll addierbare Menge bleiben unverändert.

Antworte NUR mit JSON:
{
  "add": ["neuer Eintrag"],
  "update": [
    {"existing": "exakter bisheriger Titel", "replacement": "neuer Titel mit Gesamtmenge"}
  ]
}

Verwende bei "existing" exakt den Text aus der bestehenden Einkaufsliste.
Sprache: Deutsch. Keine Kategorien oder Emojis."""
    user_input = (
        f"=== BESTEHENDE EINKAUFSLISTE ===\n{existing_text}\n\n"
        f"=== REZEPT-NOTIZ ===\n{request.recipe_text.strip()}"
    )

    try:
        raw = await run_in_threadpool(llm_query, prompt, user_input)
        data = _parse_json_response(raw)
        additions = _normalize_grocery_items(data.get("add", []))
        updates = _normalize_grocery_updates(data.get("update", []), existing_items)
        return {"add": additions, "update": updates}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Einkaufsliste konnte nicht abgeglichen werden: {exc}")


def _normalize_grocery_updates(updates: object, existing_items: list[str]) -> list[dict]:
    """Validiert Aktualisierungen gegen tatsächlich vorhandene Erinnerungstitel."""
    if not isinstance(updates, list):
        raise ValueError("Die KI-Antwort enthält keine gültigen Aktualisierungen.")

    existing_lookup = {item.casefold(): item for item in existing_items}
    normalized = []
    seen = set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        existing = " ".join(str(update.get("existing", "")).split())
        replacement = " ".join(str(update.get("replacement", "")).split())
        canonical_existing = existing_lookup.get(existing.casefold())
        if not canonical_existing or not replacement or canonical_existing.casefold() in seen:
            continue
        seen.add(canonical_existing.casefold())
        normalized.append({"existing": canonical_existing, "replacement": replacement})
    return normalized


# ============================================================
# Feature: Portionen umrechnen
# ============================================================

@app.post("/scale")
async def scale_recipe(request: ScaleRequest):
    """Rechnet Zutaten auf eine andere Portionsgröße um."""
    prompt = """Du rechnest Rezept-Mengen um. Passe ALLE Zutaten proportional an die neue Portionsgröße an.
Antworte NUR mit JSON:
{
    "title": "Rezeptname",
    "servings": "neue Portionsgröße",
    "ingredients": ["Zutat 1 mit neuer Menge", "Zutat 2 mit neuer Menge"],
    "steps": ["Schritt 1", "Schritt 2"]
}
Runde auf sinnvolle Mengen (nicht 2.67 Eier → 3 Eier). Sprache: Deutsch."""

    user_input = (
        f"Rezept: {request.title}\n"
        f"Aktuelle Portionen: {request.servings}\n"
        f"Zutaten:\n" + "\n".join(f"- {ing}" for ing in request.ingredients) +
        f"\nSchritte:\n" + "\n".join(f"- {s}" for s in request.steps) +
        f"\n\nBitte umrechnen auf: {request.target_servings}"
    )

    try:
        raw = await run_in_threadpool(llm_query, prompt, user_input)
        data = _parse_json_response(raw)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Umrechnung fehlgeschlagen: {e}")


# ============================================================
# Feature: Kategorie-Tags
# ============================================================

@app.post("/categorize")
async def categorize_recipe(request: VideoRequest):
    """Extrahiert Rezept UND vergibt automatisch Kategorie-Tags."""
    url = str(request.url)

    cached = _cache_get(url)
    if not cached:
        raise HTTPException(status_code=400, detail="Bitte zuerst /extract aufrufen, dann /categorize mit derselben URL.")

    recipe = cached
    prompt = """Analysiere das Rezept und vergib passende Tags. Antworte NUR mit JSON:
{
    "tags": ["Tag1", "Tag2", "Tag3"],
    "category": "Hauptkategorie",
    "difficulty": "Einfach/Mittel/Schwer",
    "time_estimate": "ca. X Minuten",
    "meal_type": "Frühstück/Mittagessen/Abendessen/Snack/Dessert"
}
Mögliche Tags: Vegan, Vegetarisch, Glutenfrei, Low-Carb, High-Protein, Schnell (<30min), Meal-Prep, Comfort Food, Asiatisch, Italienisch, Deutsch, Mexikanisch, Gesund, Süß, Herzhaft, One-Pot, Backen.
Sprache: Deutsch. Nur relevante Tags vergeben."""

    user_input = (
        f"Titel: {recipe['title']}\n"
        f"Portionen: {recipe['servings']}\n"
        f"Zutaten: {', '.join(recipe['ingredients'])}\n"
        f"Schritte: {' '.join(recipe['steps'])}"
    )

    try:
        raw = await run_in_threadpool(llm_query, prompt, user_input)
        data = _parse_json_response(raw)
        data["recipe_title"] = recipe["title"]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Kategorisierung fehlgeschlagen: {e}")


# ============================================================
# Feature: Nährwerte schätzen
# ============================================================

@app.post("/nutrition")
async def estimate_nutrition(request: NutritionRequest):
    """Schätzt Nährwerte pro Portion basierend auf den Zutaten."""
    prompt = """Du schätzt Nährwerte für ein Rezept basierend auf den Zutaten. Antworte NUR mit JSON:
{
    "per_serving": {
        "calories": 450,
        "protein_g": 25,
        "carbs_g": 55,
        "fat_g": 15,
        "fiber_g": 5
    },
    "total": {
        "calories": 1800,
        "protein_g": 100,
        "carbs_g": 220,
        "fat_g": 60,
        "fiber_g": 20
    },
    "health_score": "🟢 Gesund / 🟡 Okay / 🔴 Kalorienreich",
    "notes": "Kurze Ernährungseinschätzung"
}
Werte sind Schätzungen basierend auf üblichen Mengen. Sprache: Deutsch."""

    ingredients_text = "\n".join(f"- {ing}" for ing in request.ingredients)
    user_input = f"Rezept: {request.title}\nPortionen: {request.servings}\nZutaten:\n{ingredients_text}"

    try:
        raw = await run_in_threadpool(llm_query, prompt, user_input)
        data = _parse_json_response(raw)
        data["title"] = request.title
        ps = data.get("per_serving", {})
        data["formatted"] = (
            f"📊 Nährwerte pro Portion ({request.servings}):\n"
            f"  🔥 {ps.get('calories', '?')} kcal\n"
            f"  💪 Protein: {ps.get('protein_g', '?')}g\n"
            f"  🍞 Kohlenhydrate: {ps.get('carbs_g', '?')}g\n"
            f"  🧈 Fett: {ps.get('fat_g', '?')}g\n"
            f"  🌾 Ballaststoffe: {ps.get('fiber_g', '?')}g\n"
            f"\n{data.get('health_score', '')}\n{data.get('notes', '')}"
        )
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Nährwert-Schätzung fehlgeschlagen: {e}")


# ============================================================
# Feature: Wochenplan
# ============================================================

@app.post("/meal-plan")
async def meal_plan(request: MealPlanRequest):
    """Erstellt einen Wochenplan mit Rezeptvorschlägen + Gesamt-Einkaufsliste."""
    preferences = request.preferences[:MAX_REQUEST_TEXT_LEN] if request.preferences else ""
    prompt = f"""Erstelle einen Essensplan für {request.days} Tage. Antworte NUR mit JSON:
{{
    "days": [
        {{"day": "Tag 1", "lunch": "Gericht", "dinner": "Gericht"}}
    ],
    "shopping_list": {{
        "🥬 Obst & Gemüse": ["Zutat 1 mit Menge"],
        "🥩 Fleisch & Fisch": ["..."],
        "🧀 Milchprodukte": ["..."],
        "🍝 Trockenwaren": ["..."],
        "🛒 Sonstiges": ["..."]
    }},
    "total_estimated_cost": "ca. XX€"
}}
Regeln:
- Abwechslungsreich, einfach nachzukochen
- Zutaten die mehrfach vorkommen zusammenrechnen
- Realistische Mengen für 2 Personen
{f'- Präferenzen: {preferences}' if preferences else ''}
Sprache: Deutsch."""

    try:
        raw = await run_in_threadpool(llm_query, prompt, f"Erstelle einen Plan für {request.days} Tage")
        data = _parse_json_response(raw)

        lines = [f"📅 Essensplan ({request.days} Tage)", ""]
        for day in data.get("days", []):
            lines.append(f"▸ {day.get('day', '?')}")
            if day.get("lunch"):
                lines.append(f"  🥗 Mittag: {day['lunch']}")
            if day.get("dinner"):
                lines.append(f"  🍽️ Abend: {day['dinner']}")
            lines.append("")

        lines.append("🛒 Gesamt-Einkaufsliste:")
        for cat, items in data.get("shopping_list", {}).items():
            lines.append(f"\n{cat}:")
            for item in items:
                lines.append(f"  ☐ {item}")

        if data.get("total_estimated_cost"):
            lines.append(f"\n💰 Geschätzte Kosten: {data['total_estimated_cost']}")

        data["formatted_plan"] = "\n".join(lines)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Wochenplan fehlgeschlagen: {e}")


# ============================================================
# Persistente Rezeptsammlung, Planung und Ernährungstagebuch
# ============================================================

@app.post("/library/recipes")
async def save_library_recipe(request: SavedRecipeRequest):
    return await run_in_threadpool(storage.save_recipe, request.model_dump(exclude={"nutrition"}), request.nutrition)


@app.get("/library/recipes")
async def get_library_recipes():
    return await run_in_threadpool(storage.list_recipes)


@app.post("/library/recipes/{recipe_id}/nutrition")
async def calculate_library_nutrition(recipe_id: int):
    recipe = await run_in_threadpool(storage.get_recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Rezept nicht gefunden.")
    request = NutritionRequest(title=recipe["title"], ingredients=recipe["ingredients"], servings=recipe["servings"])
    nutrition = await estimate_nutrition(request)
    return await run_in_threadpool(storage.update_nutrition, recipe_id, nutrition)


@app.post("/planner/entries")
async def create_plan_entry(request: PlanEntryRequest):
    if not storage.get_recipe(request.recipe_id):
        raise HTTPException(status_code=404, detail="Rezept nicht gefunden.")
    try:
        date.fromisoformat(request.plan_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Datum muss YYYY-MM-DD sein.")
    return await run_in_threadpool(storage.add_plan, request.plan_date, request.meal_type, request.recipe_id, request.servings)


@app.get("/planner/week")
async def get_plan_week(start: Optional[str] = None):
    try:
        start_date = date.fromisoformat(start) if start else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="Datum muss YYYY-MM-DD sein.")
    return await run_in_threadpool(storage.list_plan, start_date.isoformat(), (start_date + timedelta(days=6)).isoformat())


@app.post("/tracker/log")
async def create_food_log(request: FoodLogRequest):
    recipe = storage.get_recipe(request.recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Rezept nicht gefunden.")
    nutrition = {key: recipe.get(key) for key in ("calories", "protein_g", "carbs_g", "fat_g")}
    if any(value is None for value in nutrition.values()):
        raise HTTPException(status_code=400, detail="Für dieses Rezept fehlen Nährwerte.")
    eaten_at = request.eaten_at or None
    result = await run_in_threadpool(storage.log_food, recipe["title"], request.servings, nutrition, recipe["id"], eaten_at)
    return {**result, "summary": storage.daily_summary((eaten_at or date.today().isoformat())[:10])}


@app.get("/tracker/day/{day}")
async def get_daily_log(day: str):
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="Datum muss YYYY-MM-DD sein.")
    return await run_in_threadpool(storage.daily_summary, day)


@app.put("/tracker/target")
async def update_calorie_target(request: CalorieTargetRequest):
    await run_in_threadpool(storage.set_calorie_target, request.calories)
    return {"calorie_target": request.calories}


# ============================================================
# Feature: Rezept-Bild (Karte als HTML → Screenshot-fähig)
# ============================================================

@app.post("/recipe-card")
async def recipe_card(request: VideoRequest):
    """Gibt eine hübsche Rezeptkarte als HTML zurück."""
    url = str(request.url)
    cached = _cache_get(url)
    if not cached:
        raise HTTPException(status_code=400, detail="Bitte zuerst /extract aufrufen.")

    r = cached
    html = _generate_recipe_card_html(r)
    return HTMLResponse(content=html)


def _generate_recipe_card_html(r: dict) -> str:
    """Generiert eine hübsche Rezeptkarte als HTML."""
    ingredients_html = "".join(f"<li>{html.escape(str(ing))}</li>" for ing in r.get("ingredients", []))
    steps_html = "".join(f"<li>{html.escape(str(step))}</li>" for step in r.get("steps", []))
    tips_html = f'<div class="tips">💡 {html.escape(str(r["tips"]))}</div>' if r.get("tips") else ""
    title = html.escape(str(r.get("title", "Rezept")))
    servings = html.escape(str(r.get("servings", "")))
    source_url = html.escape(str(r.get("source_url", "")))

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
         min-height: 100vh; display: flex; justify-content: center; padding: 20px; }}
  .card {{ background: white; border-radius: 24px; max-width: 500px; width: 100%;
           box-shadow: 0 20px 60px rgba(0,0,0,0.3); overflow: hidden; }}
  .header {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
             padding: 30px 24px; color: white; text-align: center; }}
  .header h1 {{ font-size: 1.6em; margin-bottom: 8px; }}
  .header .servings {{ opacity: 0.9; font-size: 1.1em; }}
  .section {{ padding: 20px 24px; }}
  .section h2 {{ font-size: 1.1em; color: #333; margin-bottom: 12px;
                 padding-bottom: 8px; border-bottom: 2px solid #f093fb; }}
  .ingredients ul {{ list-style: none; }}
  .ingredients li {{ padding: 6px 0; border-bottom: 1px solid #f0f0f0; }}
  .ingredients li::before {{ content: "•"; color: #f5576c; font-weight: bold; margin-right: 8px; }}
  .steps ol {{ padding-left: 20px; }}
  .steps li {{ padding: 8px 0; line-height: 1.5; color: #444; }}
  .tips {{ background: #fff9e6; padding: 14px 18px; margin: 16px 24px;
           border-radius: 12px; border-left: 4px solid #ffc107; font-size: 0.95em; }}
  .footer {{ text-align: center; padding: 16px; color: #999; font-size: 0.8em; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>🍳 {title}</h1>
    <div class="servings">👥 {servings}</div>
  </div>
  <div class="section ingredients">
    <h2>📝 Zutaten</h2>
    <ul>{ingredients_html}</ul>
  </div>
  <div class="section steps">
    <h2>👨‍🍳 Zubereitung</h2>
    <ol>{steps_html}</ol>
  </div>
  {tips_html}
  <div class="footer">📱 {source_url}</div>
</div>
</body>
</html>"""


# ============================================================
# Feature: Web-Dashboard — alle gecachten Rezepte anzeigen
# ============================================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Mobile Übersicht für Rezeptsammlung, Essensplan und Kalorientracker."""
    recipes = await run_in_threadpool(storage.list_recipes)
    today = date.today().isoformat()
    summary = await run_in_threadpool(storage.daily_summary, today)
    week = await run_in_threadpool(storage.list_plan, today, (date.today() + timedelta(days=6)).isoformat())

    recipe_cards = ""
    if not recipes:
        recipe_cards = '<p class="empty">Noch keine Rezepte gespeichert. Teile ein Video über den Shortcut!</p>'
    else:
        for r in recipes:
            recipe_id = int(r["id"])
            title = html.escape(str(r.get("title", "Unbekannt")))
            servings = html.escape(str(r.get("servings", "?")))
            ingredients = [html.escape(str(item)) for item in r.get("ingredients", [])]
            steps = [html.escape(str(step)) for step in r.get("steps", [])]
            ingredients_preview = ", ".join(ingredients[:5])
            tips = html.escape(str(r.get("tips", "")))
            source_url = html.escape(str(r.get("source_url", "#")), quote=True)
            calories = r.get("calories")
            nutrition_text = f"🔥 {calories:.0f} kcal pro Portion" if calories is not None else "Nährwerte noch nicht berechnet"
            recipe_cards += f"""
            <div class="recipe-card">
              <div onclick="this.parentElement.classList.toggle('expanded')">
              <h2>🍳 {title}</h2>
              <div class="meta">👥 {servings} · 📝 {len(ingredients)} Zutaten</div>
              <div class="meta">{nutrition_text}</div>
              <div class="preview">{ingredients_preview}...</div>
              </div>
              <div class="details">
                <h3>Zutaten:</h3>
                <ul>{"".join(f"<li>{ing}</li>" for ing in ingredients)}</ul>
                <h3>Zubereitung:</h3>
                <ol>{"".join(f"<li>{step}</li>" for step in steps)}</ol>
                {f'<p class="tips">💡 {tips}</p>' if tips else ""}
                <a href="{source_url}" target="_blank" rel="noopener noreferrer">📱 Original-Video</a>
                <div class="actions">
                  {f'<button onclick="nutrition({recipe_id})">Nährwerte berechnen</button>' if calories is None else f'<button onclick="logFood({recipe_id})">Heute gegessen</button>'}
                  <button class="secondary" onclick="planRecipe({recipe_id})">Einplanen</button>
                </div>
              </div>
            </div>"""

    plan_rows = "".join(
        f'<li><b>{html.escape(str(item["plan_date"]))}</b> · {html.escape(str(item["meal_type"]))}: '
        f'{html.escape(str(item["title"]))} ({item["servings"]:g} Portionen)</li>' for item in week
    ) or "<li>Noch nichts eingeplant.</li>"
    totals = summary["totals"]

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍳 Meine Rezepte</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f7; color: #333; }}
  .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
             color: white; padding: 40px 20px; text-align: center; }}
  .header h1 {{ font-size: 2em; margin-bottom: 8px; }}
  .header p {{ opacity: 0.8; }}
  .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
  .panel {{ background: white; border-radius: 16px; padding: 20px; margin-bottom: 18px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .panel h2 {{ margin-bottom: 12px; }}
  .progress {{ height: 12px; border-radius: 8px; background: #eee; overflow: hidden; margin: 10px 0; }}
  .progress span {{ display:block; height:100%; background:#667eea; width:{min(100, totals['calories'] / summary['target_calories'] * 100):.1f}%; }}
  .plan {{ list-style: none; }} .plan li {{ padding: 7px 0; border-bottom: 1px solid #eee; }}
  .stats {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .stat {{ background: white; border-radius: 16px; padding: 16px 20px;
           flex: 1; min-width: 120px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .stat .number {{ font-size: 1.8em; font-weight: 700; color: #667eea; }}
  .stat .label {{ font-size: 0.85em; color: #888; }}
  .recipe-card {{ background: white; border-radius: 16px; padding: 20px; margin-bottom: 16px;
                  box-shadow: 0 2px 8px rgba(0,0,0,0.08); cursor: pointer;
                  transition: all 0.3s; }}
  .recipe-card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,0.15); transform: translateY(-2px); }}
  .recipe-card h2 {{ font-size: 1.2em; margin-bottom: 4px; }}
  .recipe-card .meta {{ font-size: 0.85em; color: #888; margin-bottom: 8px; }}
  .recipe-card .preview {{ color: #666; font-size: 0.9em; }}
  .recipe-card .details {{ display: none; margin-top: 16px; border-top: 1px solid #eee; padding-top: 16px; }}
  .recipe-card.expanded .details {{ display: block; }}
  .recipe-card.expanded .preview {{ display: none; }}
  .details h3 {{ font-size: 1em; margin: 12px 0 8px; color: #667eea; }}
  .details ul, .details ol {{ padding-left: 20px; }}
  .details li {{ padding: 4px 0; }}
  .details .tips {{ background: #fff9e6; padding: 10px; border-radius: 8px; margin-top: 12px; }}
  .details a {{ color: #667eea; text-decoration: none; display: inline-block; margin-top: 12px; }}
  .actions {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:16px; }}
  button {{ border:0; background:#667eea; color:white; padding:11px 14px; border-radius:10px; font-weight:600; }}
  button.secondary {{ background:#eee; color:#444; }}
  .empty {{ text-align: center; color: #888; padding: 60px 20px; font-size: 1.1em; }}
</style>
</head>
<body>
<div class="header">
  <h1>🍳 Meine Rezepte</h1>
  <p>Alle Rezepte aus deinem Shortcut</p>
</div>
<div class="container">
  <div class="panel">
    <h2>🔥 Heute</h2>
    <div><b>{totals['calories']:.0f}</b> von {summary['target_calories']:.0f} kcal · noch {summary['remaining_calories']:.0f} kcal</div>
    <div class="progress"><span></span></div>
    <small>Protein {totals['protein_g']:.0f} g · Kohlenhydrate {totals['carbs_g']:.0f} g · Fett {totals['fat_g']:.0f} g</small>
    <div class="actions"><button class="secondary" onclick="setTarget()">Tagesziel ändern</button></div>
  </div>
  <div class="panel">
    <h2>📅 Nächste 7 Tage</h2>
    <ul class="plan">{plan_rows}</ul>
  </div>
  <div class="stats">
    <div class="stat"><div class="number">{len(recipes)}</div><div class="label">Rezepte</div></div>
    <div class="stat"><div class="number">{sum(len(r.get('ingredients', [])) for r in recipes)}</div><div class="label">Zutaten gesamt</div></div>
  </div>
  {recipe_cards}
</div>
<script>
async function call(url, options) {{
  const response = await fetch(url, options);
  if (!response.ok) {{ const error = await response.json(); throw new Error(error.detail || 'Das hat nicht geklappt.'); }}
  return response.json();
}}
async function nutrition(id) {{
  try {{ await call(`/library/recipes/${{id}}/nutrition`, {{method:'POST'}}); location.reload(); }}
  catch (e) {{ alert(e.message); }}
}}
async function logFood(id) {{
  const servings = prompt('Wie viele Portionen hast du gegessen?', '1');
  if (!servings) return;
  try {{ await call('/tracker/log', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{recipe_id:id, servings:Number(servings)}})}}); location.reload(); }}
  catch (e) {{ alert(e.message); }}
}}
async function planRecipe(id) {{
  const plan_date = prompt('Für welches Datum? (JJJJ-MM-TT)', '{today}');
  if (!plan_date) return;
  const meal_type = prompt('Welche Mahlzeit?', 'Abendessen');
  if (!meal_type) return;
  try {{ await call('/planner/entries', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{recipe_id:id, plan_date, meal_type, servings:1}})}}); location.reload(); }}
  catch (e) {{ alert(e.message); }}
}}
async function setTarget() {{
  const calories = prompt('Wie hoch ist dein tägliches Kalorienziel?', '{summary['target_calories']:.0f}');
  if (!calories) return;
  try {{ await call('/tracker/target', {{method:'PUT', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{calories:Number(calories)}})}}); location.reload(); }}
  catch (e) {{ alert(e.message); }}
}}
</script>
</body>
</html>"""


# ============================================================
# Hilfsfunktion: JSON aus LLM-Antwort parsen
# ============================================================

def _parse_json_response(raw: str) -> dict:
    """Parst JSON aus einer LLM-Antwort mit Markdown-Cleanup."""
    return parse_json_object(raw)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
