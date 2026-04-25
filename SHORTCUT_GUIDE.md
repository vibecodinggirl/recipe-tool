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
- **Dashboard**: Öffne `https://recipe-tool-redo.onrender.com/dashboard` im Browser um alle extrahierten Rezepte zu sehen

---

## 🛒 Shortcut: Smarte Einkaufsliste (Video + Notiz)

**Ein Shortcut für alles:** Erkennt automatisch ob du einen TikTok/Instagram-Link oder Rezepttext teilst und erstellt daraus eine kategorisierte Einkaufsliste.

**Kurzbefehle-App → + → Name: „Einkaufsliste"**

### Share Sheet aktivieren:
1. Tippe oben auf den **Namen** „Einkaufsliste"
2. Aktiviere **„Im Share Sheet anzeigen"**
3. Bei **Eingabetypen**: wähle **URLs** UND **Text** (beides!)
4. Tippe auf **Fertig**

### Aktionen hinzufügen:

#### Aktion 1: „Inhalte von URL abrufen" (Wake)
- Suche nach: **Inhalte von URL** → hinzufügen
- URL: `https://recipe-tool-redo.onrender.com/wake`
- Tippe auf das blaue **„Pfeil"**-Symbol rechts
- Methode: **GET**

#### Aktion 2: „Text"
- Suche nach: **Text** → hinzufügen
- Tippe ins Textfeld → wähle **„Kurzbefehl-Eingabe"** (lila Blase)
- → Variable setzen: Name `eingabe`

> Damit wird die Eingabe (egal ob URL oder Text) als Text gespeichert.

#### Aktion 3: „Falls"
- Suche nach: **Falls** → hinzufügen
- Falls **`eingabe`** → **beginnt mit** → `http`

> Das erkennt ob es ein Link (TikTok/Instagram) oder Text (Notiz) ist.

---

### ➡️ DANN (es ist ein Video-Link):

#### Aktion 4: „Inhalte von URL abrufen" (Rezept extrahieren)
- URL: `https://recipe-tool-redo.onrender.com/extract`
- Methode: **POST**
- Tippe auf **„Header hinzufügen"**:
  - Schlüssel: `Content-Type`
  - Wert: `application/json`
- Anforderungsinhalt: **JSON**
- **„Neues Feld hinzufügen"** → **Text**:
  - Schlüssel: `url`
  - Wert: Tippe drauf → **Variable wählen** → `eingabe`

#### Aktion 5: „Wert aus Wörterbuch abrufen"
- Schlüssel: `ingredients`
- → Variable setzen: `zutaten`

#### Aktion 6: „Wert aus Wörterbuch abrufen"
- Schlüssel: `title`
- „in": **Inhalte von URL** (Ausgabe von Aktion 4)
- → Variable setzen: `titel`

#### Aktion 7: „Inhalte von URL abrufen" (Einkaufsliste aus Zutaten)
- URL: `https://recipe-tool-redo.onrender.com/shopping-list`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- **„Neues Feld hinzufügen"** → **Text**:
  - Schlüssel: `title`
  - Wert: Variable `titel`
- **„Neues Feld hinzufügen"** → **Array**:
  - Schlüssel: `ingredients`
  - Wert: Variable `zutaten`

#### Aktion 8: „Wert aus Wörterbuch abrufen"
- Schlüssel: `formatted_list`
- → Variable setzen: `einkaufsliste`

---

### ➡️ SONST (es ist Text aus einer Notiz):

#### Aktion 9: „Inhalte von URL abrufen" (Einkaufsliste aus Text)
- URL: `https://recipe-tool-redo.onrender.com/shopping-list`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- **„Neues Feld hinzufügen"** → **Text**:
  - Schlüssel: `text`
  - Wert: Tippe drauf → **Variable wählen** → `eingabe`

#### Aktion 10: „Wert aus Wörterbuch abrufen"
- Schlüssel: `formatted_list`
- → Variable setzen: `einkaufsliste`

---

### ➡️ ENDE FALLS + Speichern:

#### Aktion 11: „Ende von Falls"
- (wird automatisch hinzugefügt)

#### Aktion 12: „Notiz erstellen"
- Suche nach: **Notiz** → „Notiz erstellen" hinzufügen
- Inhalt: Variable **`einkaufsliste`**
- Ordner: **Einkaufslisten** (erstelle diesen Ordner vorher in der Notizen-App)

#### Aktion 13: „Hinweis anzeigen"
- Text: `🛒 Einkaufsliste erstellt!`

---

### So benutzt du es:

**Von TikTok/Instagram:**
1. Video öffnen → **Teilen** → **„Einkaufsliste"** wählen
2. Rezept wird extrahiert → Einkaufsliste nach Abteilung sortiert ✅

**Von Apple Notes:**
1. Rezept-Notiz öffnen → Text markieren → **Teilen** → **„Einkaufsliste"** wählen
2. Text wird analysiert → Einkaufsliste erstellt ✅

**Manuell / Siri:**
1. **„Hey Siri, Einkaufsliste"**
2. Falls kein Text per Share Sheet → du wirst gefragt was du einfügen willst

---

## 🔢 Shortcut: Portionen umrechnen

Rechnet ein Rezept auf eine andere Personenzahl um und speichert die neue Version.

**Kurzbefehle-App öffnen → + → neuer Kurzbefehl → Name: „Portionen umrechnen"**

### Share Sheet aktivieren:
- **„Im Share Sheet anzeigen"** aktivieren
- Eingabetypen: **URLs**

### Aktionen hinzufügen:

#### Aktion 1: „Inhalte von URL abrufen" (Wake)
- URL: `https://recipe-tool-redo.onrender.com/wake`
- Methode: **GET**

#### Aktion 2: „Inhalte von URL abrufen" (Rezept holen)
- URL: `https://recipe-tool-redo.onrender.com/extract`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- Neues Feld → **Text**: Schlüssel `url`, Wert: **Kurzbefehl-Eingabe**

#### Aktion 3: „Wert aus Wörterbuch abrufen"
- Schlüssel: `title` → Variable setzen: `title`

#### Aktion 4: „Wert aus Wörterbuch abrufen"
- Schlüssel: `servings`
- „in": **Inhalte von URL** (Ausgabe von Aktion 2)
- → Variable setzen: `servings`

#### Aktion 5: „Wert aus Wörterbuch abrufen"
- Schlüssel: `ingredients`
- „in": **Inhalte von URL** (Ausgabe von Aktion 2)
- → Variable setzen: `ingredients`

#### Aktion 6: „Wert aus Wörterbuch abrufen"
- Schlüssel: `steps`
- „in": **Inhalte von URL** (Ausgabe von Aktion 2)
- → Variable setzen: `steps`

#### Aktion 7: „Nach Eingabe fragen"
- Suche nach: **Eingabe** → „Nach Eingabe fragen" hinzufügen
- Frage: `Für wie viele Personen? (z.B. "8 Portionen")`
- Eingabetyp: **Text**

#### Aktion 8: „Variable setzen"
- Name: `zielportionen`
- Eingabe: **Bereitgestellte Eingabe** (Ausgabe von Aktion 7)

#### Aktion 9: „Inhalte von URL abrufen"
- URL: `https://recipe-tool-redo.onrender.com/scale`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- **Neues Feld** → **Text**: Schlüssel `title`, Wert: Variable `title`
- **Neues Feld** → **Text**: Schlüssel `servings`, Wert: Variable `servings`
- **Neues Feld** → **Array**: Schlüssel `ingredients`, Wert: Variable `ingredients`
- **Neues Feld** → **Array**: Schlüssel `steps`, Wert: Variable `steps`
- **Neues Feld** → **Text**: Schlüssel `target_servings`, Wert: Variable `zielportionen`

#### Aktion 10: „Wert aus Wörterbuch abrufen"
- Schlüssel: `title`
- → Variable setzen: `neuer_titel`

#### Aktion 11: „Wert aus Wörterbuch abrufen"
- Schlüssel: `ingredients`
- „in": **Inhalte von URL** (Ausgabe von Aktion 9)
- → Variable setzen: `neue_zutaten`

#### Aktion 12: „Text"
- Suche nach: **Text** → hinzufügen
- Schreibe folgenden Text (tippe auf Variablen um sie einzufügen):
  ```
  🍳 neuer_titel

  📝 Zutaten:
  neue_zutaten

  ✅ Umgerechnet auf: zielportionen
  ```

#### Aktion 13: „Notiz erstellen"
- Inhalt: **Text** (Ausgabe von Aktion 12)
- Ordner: **Rezepte**

#### Aktion 14: „Hinweis anzeigen"
- Text: `✅ Rezept umgerechnet!`

---

## 📊 Shortcut: Nährwerte anzeigen

Zeigt geschätzte Kalorien und Makros pro Portion an — ohne etwas zu speichern.

**Kurzbefehle-App → + → Name: „Nährwerte"**

### Share Sheet aktivieren:
- **„Im Share Sheet anzeigen"** aktivieren
- Eingabetypen: **URLs**

### Aktionen hinzufügen:

#### Aktion 1: „Inhalte von URL abrufen" (Wake)
- URL: `https://recipe-tool-redo.onrender.com/wake`
- Methode: **GET**

#### Aktion 2: „Inhalte von URL abrufen" (Rezept holen)
- URL: `https://recipe-tool-redo.onrender.com/extract`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- Neues Feld → **Text**: Schlüssel `url`, Wert: **Kurzbefehl-Eingabe**

#### Aktion 3: „Wert aus Wörterbuch abrufen"
- Schlüssel: `title` → Variable setzen: `title`

#### Aktion 4: „Wert aus Wörterbuch abrufen"
- Schlüssel: `ingredients`
- „in": **Inhalte von URL** (Ausgabe von Aktion 2)
- → Variable setzen: `ingredients`

#### Aktion 5: „Wert aus Wörterbuch abrufen"
- Schlüssel: `servings`
- „in": **Inhalte von URL** (Ausgabe von Aktion 2)
- → Variable setzen: `servings`

#### Aktion 6: „Inhalte von URL abrufen" (Nährwerte)
- URL: `https://recipe-tool-redo.onrender.com/nutrition`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- **Neues Feld** → **Text**: Schlüssel `title`, Wert: Variable `title`
- **Neues Feld** → **Array**: Schlüssel `ingredients`, Wert: Variable `ingredients`
- **Neues Feld** → **Text**: Schlüssel `servings`, Wert: Variable `servings`

#### Aktion 7: „Wert aus Wörterbuch abrufen"
- Schlüssel: `formatted`

#### Aktion 8: „Hinweis anzeigen"
- Inhalt: **Wörterbuch-Wert** (Ausgabe von Aktion 7)
- → Zeigt ein Popup mit Kalorien, Protein, Kohlenhydrate, Fett, Ballaststoffe

---

## 🏷️ Shortcut: Rezept mit Tags speichern

Speichert das Rezept als Notiz UND zeigt automatisch erkannte Tags (Vegan, Schnell, Italienisch, etc.).

**Kurzbefehle-App → + → Name: „Rezept + Tags"**

### Share Sheet aktivieren:
- **„Im Share Sheet anzeigen"** aktivieren
- Eingabetypen: **URLs**

### Aktionen hinzufügen:

#### Aktion 1: „Inhalte von URL abrufen" (Wake)
- URL: `https://recipe-tool-redo.onrender.com/wake`
- Methode: **GET**

#### Aktion 2: „Inhalte von URL abrufen" (Rezept holen)
- URL: `https://recipe-tool-redo.onrender.com/extract`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- Neues Feld → **Text**: Schlüssel `url`, Wert: **Kurzbefehl-Eingabe**

#### Aktion 3: „Wert aus Wörterbuch abrufen"
- Schlüssel: `formatted_note`

#### Aktion 4: „Notiz erstellen"
- Inhalt: **Wörterbuch-Wert** (Ausgabe von Aktion 3)
- Ordner: **Rezepte**

#### Aktion 5: „Inhalte von URL abrufen" (Tags holen)
- URL: `https://recipe-tool-redo.onrender.com/categorize`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- Neues Feld → **Text**: Schlüssel `url`, Wert: **Kurzbefehl-Eingabe**

#### Aktion 6: „Wert aus Wörterbuch abrufen"
- Schlüssel: `tags`
- → Variable setzen: `tags`

#### Aktion 7: „Wert aus Wörterbuch abrufen"
- Schlüssel: `time_estimate`
- „in": **Inhalte von URL** (Ausgabe von Aktion 5)
- → Variable setzen: `zeit`

#### Aktion 8: „Wert aus Wörterbuch abrufen"
- Schlüssel: `difficulty`
- „in": **Inhalte von URL** (Ausgabe von Aktion 5)
- → Variable setzen: `schwierigkeit`

#### Aktion 9: „Hinweis anzeigen"
- Tippe ins Textfeld und schreibe (Variablen durch Tippen einfügen):
  ```
  ✅ Rezept gespeichert!

  🏷️ Tags: tags
  ⏱️ Zeit: zeit
  📈 Schwierigkeit: schwierigkeit
  ```

---

## 📅 Shortcut: Wochenplan erstellen

Erstellt einen Essensplan für mehrere Tage mit Gesamt-Einkaufsliste. Braucht **kein** Video — funktioniert eigenständig!

**Kurzbefehle-App → + → Name: „Wochenplan"**

> Kein Share Sheet nötig — dieser Shortcut wird direkt gestartet oder per Siri aufgerufen.

### Aktionen hinzufügen:

#### Aktion 1: „Inhalte von URL abrufen" (Wake)
- URL: `https://recipe-tool-redo.onrender.com/wake`
- Methode: **GET**

#### Aktion 2: „Nach Eingabe fragen"
- Suche nach: **Eingabe** → „Nach Eingabe fragen"
- Frage: `Für wie viele Tage? (1-7)`
- Eingabetyp: **Zahl**

#### Aktion 3: „Variable setzen"
- Name: `tage`
- Eingabe: **Bereitgestellte Eingabe**

#### Aktion 4: „Nach Eingabe fragen"
- Frage: `Wünsche? (z.B. "vegetarisch", "schnell", "italienisch" — leer lassen für alles)`
- Eingabetyp: **Text**

#### Aktion 5: „Variable setzen"
- Name: `wuensche`
- Eingabe: **Bereitgestellte Eingabe**

#### Aktion 6: „Inhalte von URL abrufen"
- URL: `https://recipe-tool-redo.onrender.com/meal-plan`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- **Neues Feld** → **Zahl**: Schlüssel `days`, Wert: Variable `tage`
- **Neues Feld** → **Text**: Schlüssel `preferences`, Wert: Variable `wuensche`

#### Aktion 7: „Wert aus Wörterbuch abrufen"
- Schlüssel: `formatted_plan`

#### Aktion 8: „Notiz erstellen"
- Inhalt: **Wörterbuch-Wert** (Ausgabe von Aktion 7)
- Ordner: **Rezepte**

#### Aktion 9: „Hinweis anzeigen"
- Text: `📅 Wochenplan erstellt & in Notizen gespeichert!`

---

## 🖼️ Shortcut: Rezeptkarte anzeigen

Öffnet eine hübsche Rezeptkarte im Browser — perfekt zum Screenshotten und per WhatsApp teilen.

**Kurzbefehle-App → + → Name: „Rezeptkarte"**

### Share Sheet aktivieren:
- **„Im Share Sheet anzeigen"** aktivieren
- Eingabetypen: **URLs**

### Aktionen hinzufügen:

#### Aktion 1: „Inhalte von URL abrufen" (Wake)
- URL: `https://recipe-tool-redo.onrender.com/wake`
- Methode: **GET**

#### Aktion 2: „Inhalte von URL abrufen" (Rezept holen)
- URL: `https://recipe-tool-redo.onrender.com/extract`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- Neues Feld → **Text**: Schlüssel `url`, Wert: **Kurzbefehl-Eingabe**

> ⚠️ Dieser Schritt ist nötig damit das Rezept im Server-Cache ist!

#### Aktion 3: „Inhalte von URL abrufen" (Rezeptkarte)
- URL: `https://recipe-tool-redo.onrender.com/recipe-card`
- Methode: **POST**
- Header: `Content-Type` = `application/json`
- Anforderungsinhalt: **JSON**
- Neues Feld → **Text**: Schlüssel `url`, Wert: **Kurzbefehl-Eingabe**

#### Aktion 4: „Schnellansicht"
- Suche nach: **Schnellansicht** → hinzufügen
- Eingabe: **Inhalte von URL** (Ausgabe von Aktion 3)
- → Zeigt die hübsche Rezeptkarte an!

#### Aktion 5: „Teilen"  (optional)
- Suche nach: **Teilen** → hinzufügen
- → Öffnet das Share Sheet damit du die Karte per WhatsApp, iMessage etc. teilen kannst
