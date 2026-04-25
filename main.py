"""
Recipe Tool - Extrahiert Rezepte aus Instagram Reels & TikTok Videos
und speichert sie in Apple Notizen via Apple Shortcut.
"""

import os
import asyncio
import hashlib
import tempfile
import logging
import time
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl
from dotenv import load_dotenv

from downloader import download_video_data, _download_audio
from extractor import transcribe_audio, extract_recipe, build_extraction_input, check_local_dependencies, MODE
from ocr import extract_text_from_frames, cleanup_frames

load_dotenv()

NOTE_STYLE = os.getenv("NOTE_STYLE", "classic")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# In-Memory Cache (max 100 Rezepte, 24h TTL)
# ============================================================
MAX_CACHE = 100
CACHE_TTL = 86400  # 24 Stunden
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()


def _cache_key(url: str) -> str:
    """Normalisiert URL und gibt einen Cache-Key zurück."""
    clean = url.split("?")[0].rstrip("/").lower()
    return hashlib.md5(clean.encode()).hexdigest()


def _cache_get(url: str) -> dict | None:
    key = _cache_key(url)
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            logger.info(f"Cache HIT für {url[:60]}")
            return data
        del _cache[key]
    return None


def _cache_set(url: str, data: dict):
    key = _cache_key(url)
    _cache[key] = (time.time(), data)
    while len(_cache) > MAX_CACHE:
        _cache.popitem(last=False)


# ============================================================
# Keep-Alive (pingt sich selbst alle 10 Min → kein Cold Start)
# ============================================================
KEEP_ALIVE_INTERVAL = 600  # 10 Minuten


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
    # Startup
    os.makedirs("tmp", exist_ok=True)
    keep_alive_task = asyncio.create_task(_keep_alive_loop())
    yield
    # Shutdown
    keep_alive_task.cancel()
    for f in os.listdir("tmp"):
        try:
            os.remove(os.path.join("tmp", f))
        except OSError:
            pass


app = FastAPI(
    title="Recipe Extractor",
    description="Extrahiert Rezepte aus Instagram Reels & TikTok Videos",
    version="1.0.0",
    lifespan=lifespan,
)


class VideoRequest(BaseModel):
    url: HttpUrl


class RecipeResponse(BaseModel):
    title: str
    servings: str
    ingredients: list[str]
    steps: list[str]
    tips: str
    source_url: str
    formatted_note: str


@app.get("/health")
async def health():
    info = {"status": "ok", "mode": MODE}
    if MODE == "local":
        info["dependencies"] = check_local_dependencies()
    return info


@app.get("/wake")
async def wake():
    """Leichtgewichtiger Endpoint zum Aufwecken des Servers (Render Cold Start)."""
    return {"status": "awake"}


@app.get("/cache/stats")
async def cache_stats():
    """Zeigt Cache-Statistiken."""
    return {"cached_recipes": len(_cache), "max_cache": MAX_CACHE, "ttl_seconds": CACHE_TTL}


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

    # oEmbed Test (mit URL-Auflösung)
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
        r = httpx.get(oembed_url, follow_redirects=True, timeout=10.0,
                      headers={"User-Agent": "Mozilla/5.0"})
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


@app.post("/extract", response_model=RecipeResponse)
async def extract_recipe_endpoint(request: VideoRequest):
    """
    Zwei-Pass-Extraktion:
    1. Schnell: Nur Caption/Metadaten holen (fast_mode)
    2. Falls zu wenig Daten: Audio runterladen + Groq Whisper Transkription
    """
    url = str(request.url)
    logger.info(f"Processing URL: {url}")

    # ===== CACHE CHECK =====
    cached = _cache_get(url)
    if cached:
        return RecipeResponse(**cached)

    # ===== PASS 1: Schnell — nur Caption/Metadaten ====="
    try:
        video_data = download_video_data(url, fast_mode=True)
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise HTTPException(status_code=400, detail=f"Video konnte nicht geladen werden: {str(e)}")

    caption = video_data.get("caption", "")
    title = video_data.get("title", "")
    subtitles = video_data.get("subtitles", "")
    audio_path = video_data["audio_path"]
    frames_dir = video_data["frames_dir"]

    # Prüfen ob Caption genug hergibt
    total_text = len(caption) + len(subtitles)
    generic_titles = ("TikTok - Make Your Day", "TikTok", "")
    title_useful = title not in generic_titles

    has_enough_caption = total_text >= 50 or title_useful
    logger.info(f"Pass 1: Caption={len(caption)}z, Subs={len(subtitles)}z, Title='{title[:50]}', genug={has_enough_caption}")

    # ===== PASS 2: Audio-Fallback wenn Caption zu kurz =====
    if not has_enough_caption:
        logger.info("Caption zu kurz — versuche Audio-Download + Transkription...")
        import uuid
        file_id = uuid.uuid4().hex[:12]
        try:
            audio_path = _download_audio(url, file_id)
        except Exception as e:
            logger.warning(f"Audio-Download fehlgeschlagen: {e}")

    try:
        # Transkription
        transcript = ""
        if subtitles:
            logger.info(f"Nutze Untertitel als Transkript ({len(subtitles)} Zeichen)")
            transcript = subtitles
        elif audio_path:
            try:
                transcript = transcribe_audio(audio_path)
            except Exception as e:
                logger.warning(f"Transkription fehlgeschlagen: {e}")

        # OCR
        ocr_text = ""
        if frames_dir:
            try:
                ocr_text = extract_text_from_frames(frames_dir)
            except Exception as e:
                logger.warning(f"OCR fehlgeschlagen: {e}")

        logger.info(f"Quellen: Audio={len(transcript)}z, Caption={len(caption)}z, OCR={len(ocr_text)}z, Titel={len(title)}z")

        # Finaler Check: genug Daten?
        total_content = len(transcript) + len(caption) + len(ocr_text)
        if total_content < 50 and not title_useful:
            raise HTTPException(
                status_code=422,
                detail="Konnte kein Rezept finden — weder in der Beschreibung noch im Audio. Funktioniert am besten bei Videos wo das Rezept im Text steht."
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

        # Rezept extrahieren
        logger.info("Extracting recipe...")
        try:
            recipe = extract_recipe(combined_input, url)
        except Exception as e:
            logger.error(f"Recipe extraction failed: {e}")
            raise HTTPException(status_code=500, detail=f"Rezept-Extraktion fehlgeschlagen: {str(e)}")

        formatted = format_for_apple_notes(recipe)
        recipe["formatted_note"] = formatted

        # Im Cache speichern
        _cache_set(url, recipe)

        return RecipeResponse(**recipe)

    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
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
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
