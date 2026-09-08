# Simple TTS

Ein kleines, selbst gehostetes Text zu Sprache Tool. Kostenlos, ohne API Key,
ohne Kontingent und ohne Zeichenlimit pro Tag. Ein Container, fertig.

* Weboberfläche zum Eintippen, Anhören und Herunterladen
* JSON und Query API für Skripte, Bots oder ein `<audio>` Tag
* Zwei Engines: online neuronale Stimmen und eine komplett offline Notlösung
* Läuft als schlankes Docker Image mit Healthcheck und ohne Rootrechte

## Engines

| Engine | Stimmen | Format | Netzwerk | Hinweis |
| --- | --- | --- | --- | --- |
| `edge` | über 300 neuronale Stimmen, mehr als 70 Sprachen | MP3 | erforderlich | Nutzt die freien Microsoft Edge Vorlesestimmen über `edge-tts`. Kein Key, kein Kontingent. |
| `espeak` | 11 Sprachen | WAV | nein | Nutzt `espeak-ng` im Container. Klingt robotisch, funktioniert aber immer. |

Standard ist `edge`. Schlägt der Aufruf fehl, springt automatisch `espeak` ein,
sofern die Anfrage keine Engine ausdrücklich vorgibt. Welche Engine geantwortet
hat, steht im Antwortheader `X-TTS-Engine`.

## Schnellstart

Mit Docker Compose:

```bash
docker compose up -d --build
```

Oder direkt mit Docker:

```bash
docker build -t simple-tts .
docker run -d --name simple-tts -p 8000:8000 --restart unless-stopped simple-tts
```

Danach http://localhost:8000 im Browser öffnen.

## Lokal ohne Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install espeak-ng      # optional, nur für die Offline Engine
uvicorn app.main:app --reload
```

## API

### `GET /api/tts`

Praktisch für Links, `curl` und `<audio src="...">`.

```bash
curl -o hallo.mp3 "http://localhost:8000/api/tts?text=Hallo%20Welt&voice=de-DE-KatjaNeural"
```

### `POST /api/tts`

```bash
curl -X POST http://localhost:8000/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Hallo Welt","voice":"de-DE-KatjaNeural","rate":10,"pitch":0}' \
  -o hallo.mp3
```

| Feld | Typ | Standard | Bedeutung |
| --- | --- | --- | --- |
| `text` | string | Pflichtfeld | Der zu sprechende Text |
| `voice` | string | Enginestandard | Stimmen ID aus `/api/voices` |
| `engine` | string | `TTS_ENGINE` | `edge` oder `espeak`. Gesetzt heißt: kein Fallback |
| `rate` | int | `0` | Tempo in Prozent, `-90` bis `200` |
| `pitch` | int | `0` | Tonhöhe, `-50` bis `50` |
| `download` | bool | `false` | Setzt `Content-Disposition: attachment` |

Antwort ist das rohe Audio mit den Headern `X-TTS-Engine` und `X-TTS-Voice`.

### Weitere Endpunkte

| Route | Zweck |
| --- | --- |
| `GET /api/voices?engine=edge&locale=de` | Stimmen einer Engine, optional nach Sprache gefiltert |
| `GET /api/engines` | Verfügbare Engines, Standard und Fallback |
| `GET /healthz` | Statusabfrage, wird auch vom Container Healthcheck genutzt |
| `GET /docs` | Automatisch erzeugte OpenAPI Oberfläche |

## Konfiguration

Alles über Umgebungsvariablen, alles optional.

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `PORT` | `8000` | Port im Container |
| `TTS_ENGINE` | `edge` | Standardengine |
| `TTS_FALLBACK_ENGINE` | `espeak` | Ersatzengine bei Fehlern, leer schaltet das ab |
| `TTS_VOICE` | leer | Erzwingt eine Standardstimme |
| `TTS_MAX_CHARS` | `20000` | Maximale Textlänge pro Anfrage |
| `TTS_MAX_CONCURRENCY` | `4` | Gleichzeitige Synthesen, schützt kleine Server |
| `TTS_CACHE_SIZE` | `128` | Zwischengespeicherte Clips im Arbeitsspeicher |
| `TTS_VOICE_CACHE_TTL` | `3600` | Sekunden bis die Stimmenliste neu geladen wird |

`TTS_MAX_CHARS` und `TTS_MAX_CONCURRENCY` sind kein Kontingent. Sie begrenzen
nur eine einzelne Anfrage beziehungsweise die Parallelität, die Anzahl der
Anfragen ist unbegrenzt.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Die Tests nutzen Stubengines und brauchen deshalb keine Netzwerkverbindung.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
