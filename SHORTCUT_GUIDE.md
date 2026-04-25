# 📱 Apple Shortcut einrichten – Schritt-für-Schritt

Rezepte aus TikTok/Instagram automatisch als Apple Note speichern.
Funktioniert mit Render-Server: `https://recipe-tool-redo.onrender.com`

---

## So funktioniert's

1. **Link teilen** → Rezept wird automatisch aus der Video-Beschreibung extrahiert
2. **Falls kein Rezept in der Beschreibung** → Shortcut fragt dich: "Kopiere den Rezepttext aus dem Video"
3. **Du fügst den Text ein** → Das LLM formatiert ihn als perfekte Notiz
4. **Fertige Notiz in Apple Notes** 🎉

---

## Shortcut erstellen

Öffne die **Kurzbefehle-App** auf deinem iPhone und erstelle einen neuen Kurzbefehl:

### Aktionen (in dieser Reihenfolge):

#### 0. „Inhalte von URL abrufen" (Server aufwecken)
- URL: `https://recipe-tool-redo.onrender.com/wake`
- Methode: **GET**
- ⚠️ **Wichtig:** Render Free-Tier schläft nach Inaktivität ein. Dieser Schritt weckt den Server auf (kann 30-60s dauern). Ohne diesen Schritt kommt es zu Zeitüberschreitungen!

#### 1. „Zwischenablage abrufen"
- Kopiere den Video-Link vorher in die Zwischenablage

#### 2. „Variable festlegen"
- Name: `videolink`
- Wert: Zwischenablage

#### 3. „Inhalte von URL abrufen" (1. API-Aufruf)
- URL: `https://recipe-tool-redo.onrender.com/extract`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Body (JSON):
  ```json
  {
    "url": "videolink"
  }
  ```

#### 4. „Wert aus Wörterbuch abrufen"
- Schlüssel: `needs_text`

#### 5. „Falls" (Bedingung)
- Falls `needs_text` **gleich** `1` (oder `true`):

  ##### 5a. „Nach Eingabe fragen"
  - Frage: `Kein Rezept in der Beschreibung gefunden. Kopiere den Text aus dem Video hier rein:`
  - Eingabetyp: **Text**

  ##### 5b. „Variable festlegen"
  - Name: `rezepttext`
  - Wert: Bereitgestellte Eingabe

  ##### 5c. „Inhalte von URL abrufen" (2. API-Aufruf mit Text)
  - URL: `https://recipe-tool-redo.onrender.com/extract`
  - Methode: **POST**
  - Header: `Content-Type` = `application/json`
  - Body (JSON):
    ```json
    {
      "url": "videolink",
      "text": "rezepttext"
    }
    ```

  ##### 5d. „Wert aus Wörterbuch abrufen"
  - Schlüssel: `formatted_note`

  ##### 5e. „Notiz erstellen"
  - Inhalt: formatted_note
  - Ordner: Rezepte

  ##### 5f. „Hinweis anzeigen"
  - `✅ Rezept gespeichert!`

- **Sonst** (Rezept wurde automatisch gefunden):

  ##### 6a. „Inhalte von URL abrufen" ← Ergebnis von Schritt 3 nutzen
  - „Wert aus Wörterbuch abrufen"
  - Schlüssel: `formatted_note`

  ##### 6b. „Notiz erstellen"
  - Inhalt: formatted_note
  - Ordner: Rezepte

  ##### 6c. „Hinweis anzeigen"
  - `✅ Rezept gespeichert!`

---

## Vereinfachte Version (ohne automatische Erkennung)

Falls dir der obige Shortcut zu komplex ist — diese Version funktioniert immer:

#### 1. „Nach Eingabe fragen"
- Frage: „Füge Link oder Rezepttext ein"

#### 2. Prüfen ob es eine URL ist
- Falls es mit `http` anfängt → als `url` schicken
- Sonst → als `text` schicken (mit `url` = `https://www.tiktok.com`)

---

## So benutzt du es

### Option A: Direkt aus dem Share Sheet (empfohlen!)

1. Öffne ein **TikTok** oder **Instagram** Video mit einem Rezept
2. Tippe auf **Teilen** (Share-Button)
3. Wähle den Kurzbefehl **„Rezept speichern"** aus der Liste
4. Fertig! Das Rezept wird automatisch in Apple Notes gespeichert ✅

### Option B: Manuell mit Zwischenablage

1. **Kopiere den Link** (Teilen → Link kopieren)
2. Öffne den **Shortcut** „Rezept speichern"
3. Warte ein paar Sekunden ⏳
4. Rezept wird als Note gespeichert ✅

---

## Share Sheet einrichten

So machst du den Kurzbefehl im Share Sheet verfügbar:

1. Öffne **Kurzbefehle-App** → deinen Kurzbefehl antippen (ℹ️-Button oder lang drücken → Details)
2. Tippe oben auf den **Namen/das Icon** des Kurzbefehls
3. Aktiviere **„Im Share Sheet anzeigen"**
4. Bei **Eingabetypen** wähle: **URLs** (nur das reicht)
5. Fertig!

### Shortcut für Share Sheet (angepasste Aktionen):

#### 0. „Inhalte von URL abrufen" (Server aufwecken)
- URL: `https://recipe-tool-redo.onrender.com/wake`
- Methode: **GET**

#### 1. „Kurzbefehl-Eingabe" verwenden
- Die URL kommt automatisch vom Share Sheet!
- **Kein** „Zwischenablage abrufen" nötig

#### 2. „Variable festlegen"
- Name: `videolink`
- Wert: **Kurzbefehl-Eingabe** (nicht Zwischenablage!)

#### 3–6: Rest bleibt gleich wie oben (ab „Inhalte von URL abrufen" POST-Aufruf)

---

## Tipps

- **Erster Aufruf langsam**: Render free tier schläft nach 15min ein — erster Aufruf braucht ~30-60s
- **TikTok funktioniert besser als Instagram** für die automatische Erkennung
- **Videos MIT Rezept in der Beschreibung** werden voll-automatisch extrahiert
- **Videos OHNE Text** → du wirst gefragt und kannst den Text manuell einfügen
- **Siri**: Du kannst den Shortcut auch per Siri aufrufen: „Hey Siri, Rezept speichern"
