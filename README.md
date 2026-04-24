# 🍳 Recipe Tool

Extrahiert automatisch Rezepte aus **Instagram Reels** und **TikTok Videos** und speichert sie in **Apple Notizen**.

**Komplett lokal & kostenlos** – läuft auf deinem Mac/PC, kein API Key nötig.

## Wie es funktioniert

```
📱 iPhone/iPad                          🖥️ Dein Mac/PC (WLAN)
┌─────────────────┐                   ┌──────────────────────┐
│ Instagram/TikTok│                   │                      │
│    ↓ Teilen     │  ── URL ──────→   │ 1. Video downloaden  │
│ Apple Shortcut  │                   │ 2. Whisper lokal     │
│    ↓            │  ←── Rezept ───   │ 3. Ollama LLM lokal  │
│ Apple Notizen ✅│                   │    (alles kostenlos!) │
└─────────────────┘                   └──────────────────────┘
```

## Quick Start (5 Minuten)

### 1. Ollama installieren

Ollama ist die lokale KI-Engine. Einmalig installieren:

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

Dann das Modell herunterladen (~4.7 GB, einmalig):

```bash
ollama pull llama3.1:8b
```

### 2. Recipe Tool installieren

```bash
cd recipe_tool
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> **Hinweis**: Beim ersten Start lädt Whisper das Sprachmodell herunter (~150 MB für `base`).

### 3. Konfiguration

```bash
cp .env.example .env
# Standard-Einstellungen sind bereits lokal – keine Änderung nötig!
```

### 4. Server starten

```bash
# Stelle sicher dass Ollama läuft:
ollama serve &

# Dann den Recipe-Server starten:
python main.py
```

Der Server läuft auf `http://localhost:8000`.

### 5. Testen

```bash
# Prüfen ob alles bereit ist:
curl http://localhost:8000/health
# → Zeigt dir ob Whisper und Ollama erkannt werden

# Rezept extrahieren:
curl -X POST http://localhost:8000/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.tiktok.com/@chef/video/123456"}'
```

### 6. Apple Shortcut einrichten

Siehe [SHORTCUT_GUIDE.md](SHORTCUT_GUIDE.md) für die Schritt-für-Schritt-Anleitung.

## iPhone mit Server verbinden

Dein Mac/PC und iPhone müssen **im gleichen WLAN** sein.

### Server-IP finden:
```bash
# macOS
ipconfig getifaddr en0

# Linux
hostname -I | awk '{print $1}'
```

Diese IP im Apple Shortcut verwenden, z.B. `http://192.168.1.42:8000`.

> **Tipp**: In deinem Router dem Mac eine feste IP zuweisen, damit sich die Adresse nicht ändert.

## Modi

| | Lokal (Standard) | API (optional) |
|---|---|---|
| Transkription | Whisper lokal | OpenAI Whisper API |
| Rezept-Extraktion | Ollama (llama3.1) | GPT-4o-mini |
| Kosten | **Kostenlos** | ~1 Cent/Rezept |
| Geschwindigkeit | ~15-30 Sek. | ~5-10 Sek. |
| Internet nötig | Nur für Video-Download | Ja |
| Setup | Ollama installieren | API Key besorgen |

Modus wechseln in `.env`:
```bash
MODE=api
OPENAI_API_KEY=sk-...
```

## API

### `POST /extract`

**Request:**
```json
{
  "url": "https://www.instagram.com/reel/ABC123/"
}
```

**Response:**
```json
{
  "title": "Cremige Pasta mit Zitrone",
  "servings": "2 Portionen",
  "ingredients": [
    "250g Spaghetti",
    "1 Zitrone (Saft und Abrieb)",
    "100ml Sahne",
    "50g Parmesan",
    "Salz und Pfeffer"
  ],
  "steps": [
    "Pasta in Salzwasser al dente kochen.",
    "Zitronensaft, Abrieb und Sahne in einer Pfanne erhitzen.",
    "Pasta mit der Sauce vermengen und Parmesan unterrühren.",
    "Mit Pfeffer abschmecken und sofort servieren."
  ],
  "tips": "Frische Kräuter wie Basilikum passen super dazu.",
  "source_url": "https://www.instagram.com/reel/ABC123/",
  "formatted_note": "🍳 Cremige Pasta mit Zitrone\n👥 2 Portionen\n..."
}
```

## Projektstruktur

```
recipe_tool/
├── main.py              # FastAPI Server & Endpunkte
├── downloader.py        # Video/Audio Download (yt-dlp)
├── extractor.py         # Transkription & Rezept-Extraktion (lokal oder API)
├── requirements.txt     # Python Dependencies
├── .env.example         # Vorlage für Umgebungsvariablen
├── .gitignore
├── SHORTCUT_GUIDE.md    # Apple Shortcut Anleitung
└── README.md
```

## Whisper Modelle (Geschwindigkeit vs. Qualität)

| Modell | Größe | Geschwindigkeit | Qualität |
|--------|-------|-----------------|----------|
| `tiny` | 39 MB | Sehr schnell | Okay für klare Sprache |
| `base` | 142 MB | Schnell | **Empfohlen** |
| `small` | 466 MB | Mittel | Sehr gut |
| `medium` | 1.5 GB | Langsam | Exzellent |
| `large` | 2.9 GB | Sehr langsam | Beste Qualität |

In `.env` ändern: `WHISPER_MODEL=small`

## Troubleshooting

| Problem | Lösung |
|---------|--------|
| `yt-dlp` Fehler | `pip install -U yt-dlp` (regelmäßig updaten) |
| Transkription leer | Video hat evtl. nur Musik, kein gesprochenes Rezept |
| Server nicht erreichbar vom iPhone | Gleiches WLAN? Firewall? Richtige IP? |
| Ollama antwortet nicht | `ollama serve` im Hintergrund starten |
| Ollama Modell nicht gefunden | `ollama pull llama3.1:8b` ausführen |
| Whisper zu langsam | Kleineres Modell wählen: `WHISPER_MODEL=tiny` |
| Erste Anfrage langsam | Normal – Whisper-Modell wird beim ersten Mal geladen |
