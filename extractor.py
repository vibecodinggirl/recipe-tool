"""
Extractor - Transkribiert Audio und extrahiert Rezepte.
Unterstützt mehrere Modi:
  - LOKAL (Standard): Whisper lokal + Ollama (komplett kostenlos)
  - OPENROUTER: OpenRouter API (kostenlos, nur Email-Registrierung)
  - GROQ: Groq Cloud (kostenlos)
  - API: OpenAI Whisper API + GPT (braucht API Key + Geld)
"""

import os
import json
import logging
import subprocess
import shutil

import httpx

logger = logging.getLogger(__name__)

# --- Konfiguration via Umgebungsvariablen ---
MODE = os.getenv("MODE", "local")  # "local", "openrouter", "groq" oder "api"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")  # tiny, base, small, medium, large

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_LLM_MODEL = os.getenv("GROQ_LLM_MODEL", "llama-3.1-8b-instant")

RECIPE_EXTRACTION_PROMPT = """Du bist ein Experte für Rezepte. Du bekommst verschiedene Informationsquellen aus einem Koch-Video.
Kombiniere ALLE verfügbaren Quellen um das bestmögliche Rezept zu extrahieren:

- AUDIO-TRANSKRIPTION: Was im Video gesprochen wird
- CAPTION/BESCHREIBUNG: Der Beschreibungstext unter dem Video
- TEXT IM VIDEO (OCR): Texteinblendungen die im Video sichtbar sind

Manche Quellen können leer sein — nutze einfach was verfügbar ist.
Wenn verschiedene Quellen unterschiedliche Infos haben, kombiniere sie intelligent.

Antworte NUR mit einem JSON-Objekt in diesem Format (keine Markdown-Codeblöcke, kein zusätzlicher Text):
{
    "title": "Name des Gerichts",
    "servings": "Portionen/Menge (z.B. '4 Portionen' oder 'ca. 12 Stück')",
    "ingredients": ["Zutat 1 mit Menge", "Zutat 2 mit Menge"],
    "steps": ["Schritt 1", "Schritt 2"],
    "tips": "Zusätzliche Tipps oder Variationen (leer lassen wenn keine)"
}

Wichtig:
- Mengenangaben bei Zutaten immer angeben wenn möglich
- Schritte kurz und klar formulieren
- Sprache: Deutsch
- Nur valides JSON zurückgeben, nichts anderes
"""


def build_extraction_input(transcript: str = "", caption: str = "", ocr_text: str = "", title: str = "") -> str:
    """Baut den Eingabetext für die Rezept-Extraktion aus allen Quellen zusammen."""
    parts = []

    if transcript and len(transcript.strip()) > 10:
        parts.append(f"=== AUDIO-TRANSKRIPTION ===\n{transcript.strip()}")

    if caption and len(caption.strip()) > 10:
        parts.append(f"=== CAPTION/BESCHREIBUNG ===\n{caption.strip()}")

    if ocr_text and len(ocr_text.strip()) > 10:
        parts.append(f"=== TEXT IM VIDEO (OCR) ===\n{ocr_text.strip()}")

    if title and len(title.strip()) > 3:
        parts.append(f"=== VIDEO-TITEL ===\n{title.strip()}")

    if not parts:
        raise ValueError("Keine verwertbaren Informationen gefunden — weder Audio, Caption noch Text im Video.")

    return "\n\n".join(parts)


# ============================================================
# Transkription
# ============================================================

def transcribe_audio(audio_path: str) -> str:
    """Transkribiert Audio – lokal, via Groq oder via OpenAI API."""
    if MODE == "openrouter":
        # Im OpenRouter-Modus: Groq Whisper nutzen wenn Key vorhanden
        if GROQ_API_KEY:
            logger.info("OpenRouter-Modus mit Groq Whisper für Transkription")
            return _transcribe_groq(audio_path)
        logger.info("OpenRouter-Modus ohne Groq Key: Keine Transkription möglich")
        return ""
    if MODE == "groq":
        return _transcribe_groq(audio_path)
    if MODE == "api":
        return _transcribe_api(audio_path)
    return _transcribe_local(audio_path)


def _transcribe_local(audio_path: str) -> str:
    """Transkribiert mit lokalem Whisper (openai-whisper Paket)."""
    logger.info(f"Transkribiere lokal mit Whisper ({WHISPER_MODEL}): {audio_path}")
    import whisper

    model = whisper.load_model(WHISPER_MODEL)
    result = model.transcribe(audio_path, language="de")
    transcript = result["text"]
    logger.info(f"Transkription ({len(transcript)} Zeichen): {transcript[:100]}...")
    return transcript


def _transcribe_groq(audio_path: str) -> str:
    """Transkribiert mit Groq Whisper API (kostenlos)."""
    logger.info(f"Transkribiere via Groq API: {audio_path}")

    with open(audio_path, "rb") as audio_file:
        response = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("audio.m4a", audio_file, "audio/m4a")},
            data={"model": "whisper-large-v3", "language": "de"},
            timeout=120.0,
        )
    response.raise_for_status()
    transcript = response.json()["text"]
    logger.info(f"Transkription ({len(transcript)} Zeichen): {transcript[:100]}...")
    return transcript


def _transcribe_api(audio_path: str) -> str:
    """Transkribiert mit OpenAI Whisper API."""
    from openai import OpenAI

    logger.info(f"Transkribiere via OpenAI API: {audio_path}")
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    with open(audio_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="de",
        )
    transcript = response.text
    logger.info(f"Transkription ({len(transcript)} Zeichen): {transcript[:100]}...")
    return transcript


# ============================================================
# Rezept-Extraktion
# ============================================================

def extract_recipe(combined_input: str, source_url: str) -> dict:
    """Extrahiert Rezept – lokal, via OpenRouter, Groq oder OpenAI API."""
    if MODE == "openrouter":
        raw = _extract_openrouter(combined_input)
    elif MODE == "groq":
        raw = _extract_groq(combined_input)
    elif MODE == "api":
        raw = _extract_api(combined_input)
    else:
        raw = _extract_ollama(combined_input)

    recipe = _parse_recipe_json(raw)
    recipe["source_url"] = source_url
    if "tips" not in recipe:
        recipe["tips"] = ""
    return recipe


def _extract_ollama(transcript: str) -> str:
    """Extrahiert Rezept mit Ollama (lokales LLM)."""
    logger.info(f"Extrahiere Rezept mit Ollama ({OLLAMA_MODEL})...")

    response = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": f"{RECIPE_EXTRACTION_PROMPT}\n\n{transcript}",
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 2000,
            },
        },
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["response"]


FALLBACK_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-120b:free",
    "z-ai/glm-4.5-air:free",
    "minimax/minimax-m2.5:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
]


def _extract_openrouter(transcript: str) -> str:
    """Extrahiert Rezept mit OpenRouter API (kostenlos, mit Retry + Fallback-Modelle)."""
    import time

    # Baue Modellliste: Primärmodell zuerst, dann max. 2 Fallbacks
    models_to_try = [OPENROUTER_MODEL]
    for m in FALLBACK_MODELS:
        if m not in models_to_try and len(models_to_try) < 3:
            models_to_try.append(m)

    last_error = None
    for model in models_to_try:
        logger.info(f"Versuche OpenRouter Modell: {model}")

        for attempt in range(2):
            try:
                response = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": RECIPE_EXTRACTION_PROMPT},
                            {"role": "user", "content": transcript},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 2000,
                    },
                    timeout=30.0,
                )
            except httpx.TimeoutException:
                logger.warning(f"Timeout bei {model} (Versuch {attempt + 1}/3)")
                last_error = f"Timeout bei Modell {model}"
                continue

            logger.info(f"OpenRouter Response: status={response.status_code}")

            if response.status_code == 429:
                try:
                    body = response.json()
                    logger.warning(f"Rate limit Details: {body}")
                except Exception:
                    logger.warning(f"Rate limit Body: {response.text[:200]}")
                wait = 5 * (attempt + 1)
                logger.warning(f"Rate limit bei {model}, warte {wait}s (Versuch {attempt + 1}/3)...")
                time.sleep(wait)
                last_error = f"Rate limit bei {model}"
                continue

            if response.status_code >= 400:
                try:
                    body = response.text[:300]
                except Exception:
                    body = "unbekannt"
                logger.warning(f"HTTP {response.status_code} bei {model}: {body}")
                last_error = f"HTTP {response.status_code} bei {model}: {body}"
                break  # Nicht retrybar → nächstes Modell

            data = response.json()

            # OpenRouter kann auch Fehler im Body zurückgeben
            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
                logger.warning(f"OpenRouter Fehler bei {model}: {error_msg}")
                last_error = f"OpenRouter Fehler bei {model}: {error_msg}"
                break  # Nächstes Modell versuchen

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content.strip():
                logger.info(f"Erfolg mit Modell {model} ({len(content)} Zeichen)")
                return content.strip()

            logger.warning(f"Leere Antwort von {model}")
            last_error = f"Leere Antwort von {model}"
            break  # Nächstes Modell

    raise RuntimeError(f"Alle OpenRouter Modelle fehlgeschlagen. Letzter Fehler: {last_error}")


def _extract_groq(transcript: str) -> str:
    """Extrahiert Rezept mit Groq API (kostenlos)."""
    logger.info(f"Extrahiere Rezept mit Groq ({GROQ_LLM_MODEL})...")

    response = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_LLM_MODEL,
            "messages": [
                {"role": "system", "content": RECIPE_EXTRACTION_PROMPT},
                {"role": "user", "content": transcript},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def _extract_api(transcript: str) -> str:
    """Extrahiert Rezept mit OpenAI GPT API."""
    from openai import OpenAI

    logger.info("Extrahiere Rezept mit OpenAI GPT...")
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": RECIPE_EXTRACTION_PROMPT},
            {"role": "user", "content": transcript},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    return response.choices[0].message.content.strip()


def _parse_recipe_json(raw_content: str) -> dict:
    """Parst die LLM-Antwort als JSON."""
    raw = raw_content.strip()

    # Markdown-Codeblöcke entfernen
    if "```" in raw:
        lines = raw.split("\n")
        json_lines = []
        inside = False
        for line in lines:
            if line.strip().startswith("```"):
                inside = not inside
                continue
            if inside or not json_lines:
                json_lines.append(line)
        raw = "\n".join(json_lines).strip()

    # Finde das JSON-Objekt in der Antwort
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]

    try:
        recipe = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"JSON Parse-Fehler: {e}\nRaw: {raw_content}")
        raise ValueError("Rezept konnte nicht aus der Transkription extrahiert werden.")

    required_fields = ["title", "servings", "ingredients", "steps"]
    for field in required_fields:
        if field not in recipe:
            raise ValueError(f"Fehlendes Feld im Rezept: {field}")

    return recipe


# ============================================================
# Hilfsfunktionen
# ============================================================

def check_local_dependencies() -> dict:
    """Prüft ob lokale Dependencies (Whisper, Ollama) verfügbar sind."""
    status = {"whisper": False, "ollama": False, "ollama_model": False}

    # Whisper check
    try:
        import whisper
        status["whisper"] = True
    except ImportError:
        pass

    # Ollama check
    if shutil.which("ollama"):
        try:
            r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
            r.raise_for_status()
            status["ollama"] = True
            models = [m["name"] for m in r.json().get("models", [])]
            status["ollama_model"] = any(OLLAMA_MODEL in m for m in models)
            status["available_models"] = models
        except Exception:
            pass

    return status
