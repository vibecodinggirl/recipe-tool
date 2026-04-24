"""
Recipe Tool - Extrahiert Rezepte aus Instagram Reels & TikTok Videos
und speichert sie in Apple Notizen via Apple Shortcut.
"""

import os
import tempfile
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl
from dotenv import load_dotenv

from downloader import download_video_data
from extractor import transcribe_audio, extract_recipe, build_extraction_input, check_local_dependencies, MODE
from ocr import extract_text_from_frames, cleanup_frames

load_dotenv()

NOTE_STYLE = os.getenv("NOTE_STYLE", "classic")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure tmp directory exists
    os.makedirs("tmp", exist_ok=True)
    yield
    # Shutdown: cleanup tmp files
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


@app.post("/extract", response_model=RecipeResponse)
async def extract_recipe_endpoint(request: VideoRequest):
    """
    Nimmt eine Video-URL (Instagram Reel oder TikTok),
    nutzt ALLE verfügbaren Quellen: Audio, Caption, Text im Video (OCR).
    """
    url = str(request.url)
    logger.info(f"Processing URL: {url}")

    # 1. Download audio + metadata + frames
    try:
        video_data = download_video_data(url)
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise HTTPException(status_code=400, detail=f"Video konnte nicht heruntergeladen werden: {str(e)}")

    audio_path = video_data["audio_path"]
    frames_dir = video_data["frames_dir"]
    subtitles = video_data.get("subtitles", "")

    try:
        # 2. Audio transkribieren ODER Untertitel nutzen
        transcript = ""
        if subtitles:
            # Untertitel gefunden — das ist die Sprache als Text, kostenlos!
            logger.info(f"Nutze Untertitel als Transkript ({len(subtitles)} Zeichen)")
            transcript = subtitles
        elif audio_path:
            # Kein Untertitel → Whisper-Transkription versuchen
            logger.info("Keine Untertitel — versuche Audio-Transkription...")
            try:
                transcript = transcribe_audio(audio_path)
            except Exception as e:
                logger.warning(f"Transkription fehlgeschlagen: {e}")

        # 3. OCR auf Video-Frames
        ocr_text = ""
        if frames_dir:
            logger.info("Running OCR on frames...")
            try:
                ocr_text = extract_text_from_frames(frames_dir)
            except Exception as e:
                logger.warning(f"OCR fehlgeschlagen: {e}")

        # 4. Alle Quellen kombinieren
        caption = video_data.get("caption", "")
        title = video_data.get("title", "")

        logger.info(f"Quellen: Audio={len(transcript)}z, Caption={len(caption)}z, OCR={len(ocr_text)}z, Titel={len(title)}z")

        try:
            combined_input = build_extraction_input(
                transcript=transcript,
                caption=caption,
                ocr_text=ocr_text,
                title=title,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        # 5. Rezept extrahieren
        logger.info("Extracting recipe...")
        recipe = extract_recipe(combined_input, url)

        # 6. Formatieren
        formatted = format_for_apple_notes(recipe)
        recipe["formatted_note"] = formatted

        return RecipeResponse(**recipe)

    finally:
        # Cleanup
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
