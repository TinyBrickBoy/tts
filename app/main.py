"""HTTP API and web interface of the text to speech service."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__
from . import engines as engine_registry
from .cache import LRUCache
from .config import settings
from .engines import Audio, Engine, TTSError, UnknownEngineError

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Simple TTS",
    version=__version__,
    description="Free, unlimited, self hosted text to speech.",
)

_cache: LRUCache[tuple, Audio] = LRUCache(settings.cache_size)
_slots = asyncio.Semaphore(max(1, settings.max_concurrency))


class SpeakRequest(BaseModel):
    """Body of a POST /api/tts call."""

    text: str = Field(min_length=1)
    voice: str | None = None
    engine: str | None = None
    rate: int = Field(default=0, ge=-90, le=200)
    pitch: int = Field(default=0, ge=-50, le=50)
    download: bool = False


#: Characters that would otherwise be dropped or mangled by the ASCII fold.
_TRANSLITERATION = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


def _slug(text: str) -> str:
    """Turn spoken text into a short, safe file name."""
    normalized = unicodedata.normalize("NFKD", text.translate(_TRANSLITERATION))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return (slug[:40].rstrip("-") or "speech")


async def _render(request: SpeakRequest) -> Audio:
    """Render the request, falling back to the offline engine where allowed."""
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be empty")
    if len(text) > settings.max_chars:
        raise HTTPException(
            status_code=413,
            detail=f"text is longer than {settings.max_chars} characters",
        )

    explicit = request.engine is not None
    try:
        engine = engine_registry.get_engine(request.engine or settings.default_engine)
    except UnknownEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    voice = request.voice or settings.default_voice or engine.default_voice
    try:
        return await _synthesize(engine, text, voice, request.rate, request.pitch)
    except TTSError as exc:
        fallback = await _fallback_for(engine) if not explicit else None
        if fallback is None:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        try:
            # The requested voice belongs to the failed engine, so use the fallback default.
            return await _synthesize(
                fallback, text, fallback.default_voice, request.rate, request.pitch
            )
        except TTSError as fallback_exc:
            raise HTTPException(
                status_code=502,
                detail=f"{exc}; fallback {fallback.name} also failed: {fallback_exc}",
            ) from fallback_exc


async def _fallback_for(failed: Engine) -> Engine | None:
    """Return the configured fallback engine when it is usable."""
    name = settings.fallback_engine
    registry = engine_registry.ENGINES
    if not name or name == failed.name or name not in registry:
        return None
    fallback = registry[name]
    return fallback if await fallback.available() else None


async def _synthesize(engine: Engine, text: str, voice: str, rate: int, pitch: int) -> Audio:
    key = (engine.name, voice, rate, pitch, text)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    async with _slots:
        # Another request may have rendered the same clip while we waited for a slot.
        cached = _cache.get(key)
        if cached is not None:
            return cached
        data = await engine.synthesize(text, voice, rate, pitch)
    audio = Audio(
        data=data,
        media_type=engine.media_type,
        extension=engine.extension,
        engine=engine.name,
        voice=voice,
    )
    _cache.put(key, audio)
    return audio


def _audio_response(audio: Audio, text: str, download: bool) -> Response:
    disposition = "attachment" if download else "inline"
    filename = f"{_slug(text)}.{audio.extension}"
    return Response(
        content=audio.data,
        media_type=audio.media_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "X-TTS-Engine": audio.engine,
            "X-TTS-Voice": audio.voice,
            "Cache-Control": "public, max-age=3600",
        },
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the single page web interface."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    """Liveness probe, also used by the container health check."""
    return {"status": "ok", "version": __version__, "cached_clips": len(_cache)}


@app.get("/api/engines")
async def list_engines() -> dict[str, object]:
    """List the engines this instance can use."""
    engines = [
        {
            "name": engine.name,
            "available": await engine.available(),
            "offline": engine.offline,
            "format": engine.extension,
            "default_voice": engine.default_voice,
        }
        for engine in engine_registry.ENGINES.values()
    ]
    return {
        "default": settings.default_engine,
        "fallback": settings.fallback_engine or None,
        "engines": engines,
    }


@app.get("/api/voices")
async def list_voices(
    engine: str | None = Query(default=None, description="Engine name, defaults to the configured one"),
    locale: str | None = Query(default=None, description="Filter by locale prefix, for example 'de'"),
) -> JSONResponse:
    """List the voices of an engine, optionally filtered by locale."""
    try:
        selected = engine_registry.get_engine(engine or settings.default_engine)
    except UnknownEngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        voices = await selected.voices()
    except TTSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if locale:
        prefix = locale.lower()
        voices = [voice for voice in voices if voice.locale.lower().startswith(prefix)]
    return JSONResponse(
        {
            "engine": selected.name,
            "count": len(voices),
            "voices": [voice.as_dict() for voice in voices],
        }
    )


@app.post("/api/tts")
async def speak(request: SpeakRequest) -> Response:
    """Render text to audio and return the audio bytes."""
    audio = await _render(request)
    return _audio_response(audio, request.text, request.download)


@app.get("/api/tts")
async def speak_via_query(
    text: str = Query(min_length=1, description="Text to speak"),
    voice: str | None = None,
    engine: str | None = None,
    rate: int = Query(default=0, ge=-90, le=200),
    pitch: int = Query(default=0, ge=-50, le=50),
    download: bool = False,
) -> Response:
    """Same as the POST route, shaped for links, audio tags and curl."""
    return await speak(
        SpeakRequest(
            text=text, voice=voice, engine=engine, rate=rate, pitch=pitch, download=download
        )
    )
