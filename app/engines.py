"""Text to speech engines.

Two engines ship with the service:

``edge``
    Uses the free Microsoft Edge read aloud voices through ``edge-tts``.
    No API key, no quota, hundreds of neural voices. Needs outbound network.

``espeak``
    Uses the ``espeak-ng`` binary that is installed in the image. Fully offline
    and therefore the fallback whenever the online engine is unavailable.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field

import edge_tts

from .config import settings


class TTSError(RuntimeError):
    """Raised when an engine cannot render the requested text."""


class UnknownEngineError(TTSError):
    """Raised when a request asks for an engine that is not registered."""


@dataclass(frozen=True)
class Voice:
    """One selectable voice."""

    id: str
    label: str
    locale: str
    gender: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "locale": self.locale,
            "gender": self.gender,
        }


@dataclass
class Audio:
    """Rendered audio plus the metadata a client needs to play or save it."""

    data: bytes
    media_type: str
    extension: str
    engine: str
    voice: str


class Engine:
    """Common interface every engine implements."""

    name: str = ""
    media_type: str = ""
    extension: str = ""
    default_voice: str = ""
    offline: bool = False

    async def available(self) -> bool:
        return True

    async def voices(self) -> list[Voice]:
        raise NotImplementedError

    async def synthesize(self, text: str, voice: str, rate: int, pitch: int) -> bytes:
        raise NotImplementedError


@dataclass
class _VoiceCache:
    """Small time based cache so the voice catalogue is not refetched per request."""

    ttl: int
    _items: list[Voice] = field(default_factory=list)
    _fetched_at: float = 0.0

    def get(self) -> list[Voice] | None:
        if self._items and (time.monotonic() - self._fetched_at) < self.ttl:
            return self._items
        return None

    def set(self, items: list[Voice]) -> list[Voice]:
        self._items = items
        self._fetched_at = time.monotonic()
        return items


class EdgeEngine(Engine):
    """Free online neural voices, served by the Microsoft Edge read aloud backend."""

    name = "edge"
    media_type = "audio/mpeg"
    extension = "mp3"
    default_voice = "de-DE-KatjaNeural"

    def __init__(self) -> None:
        self._cache = _VoiceCache(ttl=settings.voice_cache_ttl)

    async def voices(self) -> list[Voice]:
        cached = self._cache.get()
        if cached is not None:
            return cached
        try:
            raw = await edge_tts.list_voices()
        except Exception as exc:  # network or upstream format problem
            raise TTSError(f"voice catalogue unavailable: {exc}") from exc
        voices = [
            Voice(
                id=item["ShortName"],
                label=self._label(item),
                locale=item.get("Locale", ""),
                gender=item.get("Gender", ""),
            )
            for item in raw
        ]
        voices.sort(key=lambda voice: (voice.locale, voice.id))
        return self._cache.set(voices)

    @staticmethod
    def _label(item: dict) -> str:
        # "de-DE-KatjaNeural" -> "Katja"
        short_name = item.get("ShortName", "")
        display = short_name.split("-")[-1].removesuffix("Neural") or short_name
        locale = item.get("Locale", "")
        gender = item.get("Gender", "")
        return f"{display} ({locale}, {gender})" if gender else f"{display} ({locale})"

    async def synthesize(self, text: str, voice: str, rate: int, pitch: int) -> bytes:
        communicate = edge_tts.Communicate(
            text,
            voice or self.default_voice,
            rate=f"{rate:+d}%",
            pitch=f"{pitch:+d}Hz",
        )
        chunks = bytearray()
        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks += chunk["data"]
        except Exception as exc:
            raise TTSError(f"edge synthesis failed: {exc}") from exc
        if not chunks:
            raise TTSError("edge synthesis returned no audio")
        return bytes(chunks)


class EspeakEngine(Engine):
    """Offline formant synthesis through the espeak-ng binary."""

    name = "espeak"
    media_type = "audio/wav"
    extension = "wav"
    default_voice = "de"
    offline = True

    #: espeak-ng speaks 175 words per minute by default.
    _BASE_WPM = 175

    _LANGUAGES = [
        ("de", "Deutsch"),
        ("en", "English"),
        ("en-us", "English (US)"),
        ("es", "Espanol"),
        ("fr", "Francais"),
        ("it", "Italiano"),
        ("nl", "Nederlands"),
        ("pl", "Polski"),
        ("pt", "Portugues"),
        ("ru", "Russkij"),
        ("tr", "Turkce"),
    ]

    def __init__(self) -> None:
        self._binary = shutil.which("espeak-ng") or shutil.which("espeak")

    async def available(self) -> bool:
        return self._binary is not None

    async def voices(self) -> list[Voice]:
        if self._binary is None:
            return []
        return [
            Voice(id=code, label=f"{label} ({code})", locale=code)
            for code, label in self._LANGUAGES
        ]

    async def synthesize(self, text: str, voice: str, rate: int, pitch: int) -> bytes:
        if self._binary is None:
            raise TTSError("espeak-ng is not installed in this container")
        # Map the shared percent based controls onto the espeak scales.
        words_per_minute = max(80, min(450, round(self._BASE_WPM * (1 + rate / 100))))
        espeak_pitch = max(0, min(99, 50 + pitch))
        process = await asyncio.create_subprocess_exec(
            self._binary,
            "-v",
            voice or self.default_voice,
            "-s",
            str(words_per_minute),
            "-p",
            str(espeak_pitch),
            "--stdout",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(text.encode("utf-8"))
        if process.returncode != 0 or not stdout:
            detail = stderr.decode("utf-8", "replace").strip() or "no audio produced"
            raise TTSError(f"espeak synthesis failed: {detail}")
        return stdout


ENGINES: dict[str, Engine] = {engine.name: engine for engine in (EdgeEngine(), EspeakEngine())}


def get_engine(name: str) -> Engine:
    """Look an engine up by name, raising a typed error for unknown names."""
    try:
        return ENGINES[name]
    except KeyError:
        known = ", ".join(sorted(ENGINES))
        raise UnknownEngineError(f"unknown engine '{name}', available: {known}") from None
