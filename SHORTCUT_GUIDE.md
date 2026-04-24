# 📱 Apple Shortcut einrichten – Schritt-für-Schritt

Mit diesem Apple Shortcut kannst du direkt aus Instagram oder TikTok ein Rezept extrahieren
und automatisch in Apple Notizen speichern.

---

## Shortcut erstellen

Öffne die **Kurzbefehle-App** auf deinem iPhone/iPad und erstelle einen neuen Kurzbefehl:

### Aktionen (in dieser Reihenfolge):

#### 1. „Eingabe vom Teilen-Menü empfangen"
- Typ: **URLs**
- Dies ermöglicht, den Shortcut direkt aus Instagram/TikTok über das Teilen-Menü aufzurufen.

#### 2. „URL abrufen" (API-Aufruf)
- URL: `http://DEINE-SERVER-IP:8000/extract`
- Methode: **POST**
- Header:
  - `Content-Type`: `application/json`
- Body (JSON):
  ```json
  {
    "url": "Kurzbefehlseingabe"
  }
  ```
  (Wähle die Variable „Kurzbefehlseingabe" als Wert für `url`)

#### 3. „Wörterbuch aus Eingabe" (optional – für Fehlerbehandlung)
- Falls Statuscode ≠ 200: Hinweis anzeigen „Rezept konnte nicht extrahiert werden"

#### 4. „Wert aus Wörterbuch abrufen"
- Schlüssel: `formatted_note`

#### 5. „Notiz erstellen" (Apple Notes)
- Inhalt: Wert aus dem vorherigen Schritt (formatted_note)
- Ordner: „Rezepte" (erstelle diesen Ordner vorher in Apple Notizen)

#### 6. „Hinweis anzeigen" (Bestätigung)
- Text: `✅ Rezept gespeichert!`

---

## So benutzt du es

1. Öffne ein **Instagram Reel** oder **TikTok Video** mit einem Rezept
2. Tippe auf **Teilen** (Share-Button)
3. Wähle **„Rezept speichern"** (dein Shortcut-Name)
4. Warte ein paar Sekunden ⏳
5. Das Rezept erscheint in **Apple Notizen** im Ordner „Rezepte" 🎉

---

## Alternative: Shortcut mit manueller URL-Eingabe

Falls du den Link kopierst statt zu teilen:

#### 1. „Nach Eingabe fragen"
- Typ: Text
- Frage: „Füge den Link zum Rezept-Video ein"

#### 2. Dann weiter wie oben ab Schritt 2

---

## Tipps

- **Server erreichbar machen**: Der Server muss vom iPhone aus erreichbar sein:
  - **Zuhause**: Server auf einem Mac/PC im gleichen WLAN starten → lokale IP verwenden
  - **Unterwegs**: Server auf einem VPS hosten oder einen Tunnel (z.B. Tailscale, ngrok) nutzen

- **Schnelltipp ngrok**: Für schnelles Testen:
  ```bash
  ngrok http 8000
  ```
  Dann die ngrok-URL im Shortcut verwenden.

- **Siri**: Du kannst den Shortcut auch per Siri aufrufen:
  „Hey Siri, Rezept speichern"
