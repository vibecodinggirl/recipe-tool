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

## Essensplan und Kalorientracker

Unter `/dashboard` findest du eine gemeinsame, für das Handy geeignete Übersicht:

- extrahierte Rezepte werden automatisch in der Rezeptsammlung gespeichert,
- fehlende Nährwerte lassen sich mit einem Knopfdruck schätzen,
- Rezepte können für ein Datum eingeplant und als gegessen eingetragen werden,
- Kalorien und Makronährstoffe werden pro Tag zusammengerechnet.

Lokal werden die Daten automatisch in `data/recipe_tool.db` gespeichert. Auf Render
sollte `DATABASE_URL` auf eine dauerhafte PostgreSQL-Datenbank zeigen. Für Supabase:

1. Ein kostenloses Projekt anlegen.
2. Unter **Project Settings → Database** den Connection-String kopieren.
3. In Render eine geheime Umgebungsvariable `DATABASE_URL` mit diesem Wert anlegen.
4. Den Render-Dienst neu starten. Die Tabellen werden automatisch erstellt.

Am bestehenden Apple-Kurzbefehl muss dafür nichts geändert werden.

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

### `POST /shortcut`

Minimaler Endpunkt für Apple Kurzbefehle. Er erledigt die komplette Extraktion und
liefert direkt die fertig formatierte Notiz als Klartext. Im Kurzbefehl sind deshalb
nur „Inhalte von URL abrufen“ und „Notiz erstellen“ nötig.

```json
{
  "url": "GETEILTE_URL"
}
```

Alternativ steht für einfache Integrationen auch
`GET /shortcut?url=GETEILTE_URL` zur Verfügung.

### `POST /smart-grocery-list`

Erstellt aus bis zu zehn Rezept-Notizen eine gemeinsame Einkaufsliste. Der
Endpunkt entfernt Duplikate, vereinheitlicht Schreibweisen, addiert kompatible
Mengen und kann jedes Rezept vorab auf dieselbe Personenzahl skalieren.

```json
{
  "recipes": [
    "Rezept-Notiz 1 ...",
    "Rezept-Notiz 2 ..."
  ],
  "target_servings": 4
}
```

Ohne `target_servings` bleiben die ursprünglichen Mengen erhalten. Die Antwort
enthält `items` als saubere Liste für Apple Erinnerungen.

### `POST /merge-grocery-list`

Vergleicht eine Rezept-Notiz mit den bereits offenen Einträgen der
Apple-Einkaufsliste. Gleiche Zutaten werden auch bei unterschiedlichen
Mengenangaben erkannt. Kompatible Mengen werden addiert.

```json
{
  "recipe_text": "Für das Rezept: 3 Eier und 1 Zitrone",
  "existing_items": ["2 Eier", "Milch"]
}
```

Die Antwort trennt neue Einträge von Aktualisierungen:

```json
{
  "add": ["1 Zitrone"],
  "update": [
    {"existing": "2 Eier", "replacement": "5 Eier"}
  ]
}
```

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
├── json_utils.py        # Gemeinsames, robustes Parsen von KI-Antworten
├── storage.py           # Rezeptsammlung, Essensplan und Ernährungstagebuch
├── tests/               # Automatisierte Offline-Tests
├── requirements.txt     # Python Dependencies
├── requirements-cloud.txt # Schlanke Dependencies für Docker/Render
├── requirements-dev.txt # Test-Dependencies
├── .env.example         # Vorlage für Umgebungsvariablen
├── .gitignore
├── SHORTCUT_GUIDE.md    # Apple Shortcut Anleitung
└── README.md
```

## Vorhandene Funktionen

- Rezept-Extraktion synchron oder als Hintergrundjob
- Transkription über lokales Whisper, Groq oder OpenAI
- Rezeptanalyse über Ollama, OpenRouter, Groq oder OpenAI
- Caption-, Untertitel- und optional OCR-Auswertung
- Apple-Notizen-Formatierung in vier Stilen
- Einkaufsliste, Portionsumrechnung, Kategorien und Nährwertschätzung
- Vorratsauswahl vor dem Übertragen in Apples intelligente Einkaufsliste
- Zusammenführen mehrerer Rezepte mit Mengenaddition und optionaler Skalierung
- Persistente Rezeptsammlung, Essensplan und Kalorientracker im mobilen Dashboard
- In-Memory-Cache mit Ablaufzeit sowie Job-Verwaltung

## Tests

Die Offline-Tests benötigen keine API-Schlüssel und laden keine Videos herunter:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
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
