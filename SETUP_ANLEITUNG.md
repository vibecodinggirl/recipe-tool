# Rezept-Tool: Komplette Anleitung

Diese Anleitung führt dich Schritt für Schritt durch die Installation.
Du brauchst keine Programmierkenntnisse. Einfach die Befehle kopieren und einfügen.

Voraussetzung: Ein Mac (oder MacBook) und ein iPhone im gleichen WLAN.

---

## TEIL 1 — Mac vorbereiten

### Schritt 1: Terminal öffnen

1. Drücke `Cmd + Leertaste` auf deiner Tastatur
2. Tippe: **Terminal**
3. Drücke Enter

Es öffnet sich ein schwarzes/weißes Fenster mit Text. Das ist das Terminal.
Hier wirst du gleich Befehle reinkopieren.


### Schritt 2: Homebrew installieren

Homebrew ist ein Paketmanager für Mac — damit installierst du Programme.

Kopiere diesen Befehl und füge ihn ins Terminal ein (Cmd+V), dann Enter:

```
brew --version
```

**Wenn eine Versionsnummer kommt** (z.B. "Homebrew 4.x.x"):
→ Alles gut, weiter mit Schritt 3.

**Wenn "command not found" kommt:**
→ Kopiere diesen Befehl ins Terminal und drücke Enter:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

- Es fragt nach deinem Mac-Passwort → eintippen (man sieht nichts beim Tippen, das ist normal!)
- Es zeigt dir am Ende evtl. 2 Befehle die du noch ausführen sollst → diese auch kopieren und ausführen
- Warte bis es fertig ist (dauert ein paar Minuten)


### Schritt 3: Python prüfen

Kopiere ins Terminal:

```
python3 --version
```

**Wenn "Python 3.11" oder höher kommt:**
→ Alles gut, weiter.

**Wenn "command not found" oder eine alte Version:**

```
brew install python@3.12
```


### Schritt 4: ffmpeg installieren

ffmpeg wird gebraucht um Audio aus Videos zu extrahieren.

```
brew install ffmpeg
```

Warte bis es fertig ist.


---

## TEIL 2 — Ollama installieren (die lokale KI)

Ollama ist die KI die auf deinem Mac läuft — kostenlos, kein Account nötig.


### Schritt 5: Ollama installieren

```
brew install ollama
```


### Schritt 6: Ollama starten

```
ollama serve
```

**WICHTIG:** Dieses Terminal-Fenster muss offen bleiben!
Es zeigt "Listening on 127.0.0.1:11434" oder ähnlich.

Öffne jetzt ein NEUES Terminal-Fenster:
→ Drücke `Cmd + N` (oder `Cmd + T` für einen neuen Tab)


### Schritt 7: KI-Modell herunterladen

Im neuen Terminal-Fenster:

```
ollama pull llama3.1:8b
```

Das lädt ca. 5 GB herunter. Warte bis es fertig ist.
Je nach Internet dauert das 5-20 Minuten.

Du siehst einen Fortschrittsbalken.


### Schritt 8: Testen ob die KI funktioniert

```
ollama run llama3.1:8b "Sag einfach Hallo"
```

**Wenn eine Antwort kommt** (irgendwas mit "Hallo"):
→ Super, die KI läuft! Drücke `Ctrl + D` um wieder rauszukommen.

**Wenn ein Fehler kommt:**
→ Geh zurück zum anderen Terminal-Fenster und schau ob `ollama serve` noch läuft.


---

## TEIL 3 — Recipe Tool installieren

### Schritt 9: Ordner auf den Mac kopieren

Kopiere den Ordner `recipe_tool` auf deinen Mac Desktop.
Egal wie: USB-Stick, AirDrop, iCloud, Email...

Hauptsache der Ordner liegt auf dem Desktop.


### Schritt 10: Ins Projekt navigieren

Im Terminal (das aus Schritt 7, nicht das mit ollama serve):

```
cd ~/Desktop/recipe_tool
```


### Schritt 11: Python-Umgebung erstellen

```
python3 -m venv venv
```

Dann:

```
source venv/bin/activate
```

**Check:** Links im Terminal sollte jetzt `(venv)` stehen.
Wenn ja → weiter. Wenn nicht → den source-Befehl nochmal ausführen.


### Schritt 12: Alle Pakete installieren

```
pip install -r requirements.txt
```

Das dauert ein paar Minuten. Es werden viele Sachen heruntergeladen.
Warte bis du wieder die Eingabezeile siehst.


### Schritt 13: Konfiguration erstellen

```
cp .env.example .env
```

Das war's schon. Die Standard-Einstellungen passen.

**Optional — Notiz-Style ändern:**
Wenn du willst, öffne die Datei:

```
nano .env
```

Dort findest du die Zeile `NOTE_STYLE=classic`.
Ändere zu einem dieser Werte wenn du willst:
- `classic`   → Mit Emojis, übersichtlich
- `minimal`   → Nur Text, clean
- `card`      → Rezeptkarte mit Rahmen
- `checklist` → Zum Abhaken beim Kochen

Speichern: `Ctrl + O` dann Enter. Schließen: `Ctrl + X`.


---

## TEIL 4 — Server starten & testen

### Schritt 14: Server starten

Stelle sicher du bist noch im richtigen Ordner mit aktivierter venv:
(Du siehst `(venv)` links im Terminal)

```
python main.py
```

Es sollte kommen:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**WICHTIG:** Auch dieses Terminal-Fenster muss offen bleiben!

Du hast jetzt also 2 Terminal-Fenster offen:
1. `ollama serve` (aus Schritt 6)
2. `python main.py` (gerade eben)


### Schritt 15: Testen ob alles funktioniert

Öffne ein DRITTES Terminal-Fenster (`Cmd + N`):

```
curl http://localhost:8000/health
```

Du solltest sowas sehen:
```
{"status":"ok","mode":"local","dependencies":{"whisper":true,"ollama":true,"ollama_model":true}}
```

**Alles `true`?** → Perfekt, alles läuft!

**`whisper: false`?**
```
cd ~/Desktop/recipe_tool && source venv/bin/activate && pip install openai-whisper
```

**`ollama: false`?**
→ Prüfe ob `ollama serve` im anderen Terminal noch läuft.

**`ollama_model: false`?**
```
ollama pull llama3.1:8b
```


---

## TEIL 5 — iPhone mit Mac verbinden

### Schritt 16: Sicherstellen dass iPhone und Mac im gleichen WLAN sind

- Mac: Klicke oben rechts auf das WLAN-Symbol → Name des Netzwerks merken
- iPhone: Einstellungen → WLAN → gleiches Netzwerk?

Beide müssen im GLEICHEN Netzwerk sein!


### Schritt 17: IP-Adresse des Macs herausfinden

Im dritten Terminal-Fenster:

```
ipconfig getifaddr en0
```

Da kommt eine Nummer wie z.B.: `192.168.1.42`

**DIESE NUMMER AUFSCHREIBEN ODER MERKEN!** Du brauchst sie gleich.

Falls nichts kommt, probiere:
```
ipconfig getifaddr en1
```

Oder: Systemeinstellungen → WLAN → Details → IP-Adresse ablesen.


### Schritt 18: Vom iPhone aus testen

1. Öffne **Safari** auf dem iPhone
2. Tippe in die Adresszeile:
   `http://192.168.1.42:8000/health`
   (ersetze 192.168.1.42 mit DEINER IP aus Schritt 17!)
3. Drücke Enter

**Wenn du JSON-Text siehst** (mit "status":"ok"):
→ Die Verbindung funktioniert!

**Wenn die Seite nicht geladen wird:**
→ Prüfe: Gleiches WLAN? Server läuft noch? IP richtig?
→ Mac Firewall: Systemeinstellungen → Netzwerk → Firewall → deaktivieren zum Testen


---

## TEIL 6 — Apple Shortcut erstellen

### Schritt 19: Rezepte-Ordner erstellen

1. Öffne die **Notizen-App** auf dem iPhone
2. Tippe auf "Ordner" (oben links falls nötig, zurück zur Übersicht)
3. Tippe auf das Ordner-Symbol unten links (neuer Ordner)
4. Name: **Rezepte**
5. Tippe auf "Sichern"


### Schritt 20: Kurzbefehle-App öffnen

1. Suche auf dem iPhone nach der App **Kurzbefehle** (Shortcuts)
2. Falls nicht installiert: App Store → "Kurzbefehle" suchen → installieren
3. Öffne die App


### Schritt 21: Neuen Kurzbefehl erstellen

1. Tippe oben rechts auf das **+**
2. Oben steht "Neuer Kurzbefehl" → tippe darauf und benenne ihn: **Rezept speichern**


### Schritt 22: Teilen-Menü aktivieren

Das ist der wichtigste Schritt — damit der Shortcut im Teilen-Menü von Instagram/TikTok auftaucht.

**Methode A (iOS 16+):**
1. Du bist im neuen Kurzbefehl (noch leer)
2. Tippe oben auf den **Namen "Rezept speichern"** (oder auf das kleine **i** bzw. den **Pfeil nach unten** neben dem Namen)
3. Es öffnet sich ein Menü
4. Tippe auf **"Im Teilen-Menü anzeigen"** → Schalter **aktivieren** (grün)
5. Darunter steht "Empfängt": Tippe darauf
6. Deaktiviere alles AUSSER **URLs**
7. Tippe auf **"Fertig"** um zurückzukommen

**Methode B (falls du es nicht findest):**
1. Tippe auf **"Aktion hinzufügen"**
2. Suche nach: **Teilen** oder **Share**
3. Suche nach: **"Empfängt Eingaben von"** oder **"Receive input from"**
4. Falls du gar nichts findest: Suche nach **"Eingabe"**
5. Tippe auf **"Beliebig"** → nur **URLs** aktivieren

**Methode C (Kurzbefehl-Details):**
1. Tippe oben rechts auf das **Schieberegler-Symbol** (⚙️ oder drei Striche)
2. Oder tippe lange auf den Shortcut in der Übersicht → **"Details"**
3. Dort findest du **"Im Teilen-Menü anzeigen"**
4. Aktivieren + bei Empfangstyp nur **URLs** auswählen


### Schritt 23: Aktion 2 — URL abrufen (API-Aufruf)

1. Tippe unten auf das **Suchfeld** → suche: **URL abrufen**
2. Wähle: **"URL abrufen"**
3. Im URL-Feld tippe:
   **http://192.168.1.42:8000/extract**
   (DEINE IP von Schritt 17 verwenden!)
4. Tippe auf **"Erweitert einblenden"** (kleiner Pfeil)
5. Ändere Methode von GET zu: **POST**
6. Bei **Header**:
   - Tippe "Neuen Header hinzufügen"
   - Schlüssel: `Content-Type`
   - Text: `application/json`
7. Bei **Anfrageninhalt** (Body):
   - Tippe auf "Anfrageninhalt" → wähle **JSON**
   - Tippe auf "Neues Feld hinzufügen" → **Text**
   - Schlüssel: `url`
   - Wert: Tippe ins Wert-Feld → tippe dann auf **"Kurzbefehlseingabe"** (blaue Variable oberhalb der Tastatur)


### Schritt 24: Aktion 3 — Wert aus Antwort holen

1. Suche: **Wörterbuch**
2. Wähle: **"Wert aus Wörterbuch abrufen"**
3. Bei "Schlüssel" tippe: **formatted_note**
4. Bei "Wörterbuch" sollte automatisch **"Inhalt der URL"** stehen
   (falls nicht → tippe drauf und wähle "Inhalt der URL")


### Schritt 25: Aktion 4 — Notiz erstellen

1. Suche: **Notiz erstellen**
2. Wähle: **"Notiz erstellen"** (mit dem Notizen-App-Symbol)
3. Bei "Inhalt/Body": Tippe ins Feld → wähle die Variable **"Wörterbuchwert"** (blaue Variable)
4. Bei "Ordner": Tippe drauf → wähle den Ordner **"Rezepte"** (den du in Schritt 19 erstellt hast)
5. Falls "Beim Ausführen fragen" erscheint → **deaktivieren** (damit es automatisch geht)


### Schritt 26: Aktion 5 — Bestätigung anzeigen

1. Suche: **Hinweis**
2. Wähle: **"Hinweis anzeigen"**
3. Tippe ins Textfeld: **Rezept gespeichert!**


### Schritt 27: Shortcut speichern

1. Tippe oben rechts auf **"Fertig"**
2. Der Shortcut ist jetzt gespeichert!


---

## TEIL 7 — Erster Test! 🎉

### Schritt 28: Server-Check

Stelle sicher auf deinem Mac:
- Terminal 1: `ollama serve` läuft noch
- Terminal 2: `python main.py` läuft noch
- Beide Fenster offen lassen!


### Schritt 29: Rezept-Video finden

1. Öffne **Instagram** oder **TikTok** auf dem iPhone
2. Finde ein Rezept-Video wo jemand **spricht** und das Rezept erklärt
   (Videos mit nur Musik funktionieren NICHT — es muss gesprochen werden!)


### Schritt 30: Teilen!

1. Tippe auf den **Teilen-Button** (Pfeil-Symbol)
2. Scrolle in der unteren Reihe nach rechts
3. Suche **"Rezept speichern"** (dein Shortcut)
   - Falls du ihn nicht siehst: Ganz nach rechts scrollen → "Mehr" → dort findest du ihn
4. Tippe drauf!


### Schritt 31: Warten

- Es dauert ca. 15-30 Sekunden
- Auf dem Mac siehst du im Terminal Aktivität (Downloads, Transkription...)
- Auf dem iPhone erscheint am Ende: **"Rezept gespeichert!"**


### Schritt 32: Rezept anschauen

1. Öffne die **Notizen-App**
2. Gehe in den Ordner **"Rezepte"**
3. Dein Rezept ist da! 🎉


---

## Jeden Tag benutzen

### Server starten (jeden Tag wenn du ihn brauchst)

Öffne Terminal und führe diese 3 Befehle aus:

```
ollama serve &
cd ~/Desktop/recipe_tool
source venv/bin/activate
python main.py
```

### Server stoppen (wenn du fertig bist)

Im Terminal wo `python main.py` läuft: `Ctrl + C`
Ollama stoppen: `pkill ollama`


---

## Probleme & Lösungen

| Was passiert | Was tun |
|---|---|
| "Server nicht erreichbar" auf iPhone | Mac und iPhone im gleichen WLAN? Server noch an? |
| Shortcut zeigt Fehler | IP-Adresse im Shortcut nochmal prüfen |
| "Transkription zu kurz" | Das Video hat nur Musik — probiere eins wo jemand spricht |
| Server stürzt ab | Terminal-Fehler lesen. Oft fehlt ffmpeg → `brew install ffmpeg` |
| "Ollama not found" | Neues Terminal öffnen → `ollama serve` starten |
| Alles sehr langsam | Normal beim ersten Mal! Whisper lädt das Modell. Danach schneller. |
| IP hat sich geändert | `ipconfig getifaddr en0` nochmal → neue IP im Shortcut eintragen |
