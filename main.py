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
from collections import OrderedDict
from contextlib import asynccontextmanager

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
    version="1.3.0",
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


class NutritionRequest(BaseModel):
    title: str
    ingredients: list[str]
    servings: str = ""


class MealPlanRequest(BaseModel):
    days: int = Field(default=5, ge=1, le=7)
    preferences: str = ""


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
    """Einfaches Web-Dashboard mit allen gespeicherten Rezepten."""
    with _cache_lock:
        recipes = [copy.deepcopy(data) for _, data in _cache.values()]

    recipe_cards = ""
    if not recipes:
        recipe_cards = '<p class="empty">Noch keine Rezepte gespeichert. Teile ein Video über den Shortcut!</p>'
    else:
        for r in recipes:
            title = html.escape(str(r.get("title", "Unbekannt")))
            servings = html.escape(str(r.get("servings", "?")))
            ingredients = [html.escape(str(item)) for item in r.get("ingredients", [])]
            steps = [html.escape(str(step)) for step in r.get("steps", [])]
            ingredients_preview = ", ".join(ingredients[:5])
            tips = html.escape(str(r.get("tips", "")))
            source_url = html.escape(str(r.get("source_url", "#")), quote=True)
            recipe_cards += f"""
            <div class="recipe-card" onclick="this.classList.toggle('expanded')">
              <h2>🍳 {title}</h2>
              <div class="meta">👥 {servings} · 📝 {len(ingredients)} Zutaten</div>
              <div class="preview">{ingredients_preview}...</div>
              <div class="details">
                <h3>Zutaten:</h3>
                <ul>{"".join(f"<li>{ing}</li>" for ing in ingredients)}</ul>
                <h3>Zubereitung:</h3>
                <ol>{"".join(f"<li>{step}</li>" for step in steps)}</ol>
                {f'<p class="tips">💡 {tips}</p>' if tips else ""}
                <a href="{source_url}" target="_blank" rel="noopener noreferrer">📱 Original-Video</a>
              </div>
            </div>"""

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
  .empty {{ text-align: center; color: #888; padding: 60px 20px; font-size: 1.1em; }}
</style>
</head>
<body>
<div class="header">
  <h1>🍳 Meine Rezepte</h1>
  <p>Alle Rezepte aus deinem Shortcut</p>
</div>
<div class="container">
  <div class="stats">
    <div class="stat"><div class="number">{len(recipes)}</div><div class="label">Rezepte</div></div>
    <div class="stat"><div class="number">{sum(len(r.get('ingredients', [])) for r in recipes)}</div><div class="label">Zutaten gesamt</div></div>
  </div>
  {recipe_cards}
</div>
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
