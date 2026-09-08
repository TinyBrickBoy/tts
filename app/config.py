"""Runtime configuration, all of it overridable through environment variables."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


class Settings:
    """Configuration values read once at import time."""

    #: Engine used when the request does not ask for a specific one.
    default_engine: str = os.environ.get("TTS_ENGINE", "edge")

    #: Engine used when the default one fails. Empty string disables the fallback.
    fallback_engine: str = os.environ.get("TTS_FALLBACK_ENGINE", "espeak")

    #: Voice used when the request does not ask for a specific one.
    default_voice: str = os.environ.get("TTS_VOICE", "")

    #: Upper bound for a single request. There is no quota, only a per request size.
    max_chars: int = _int("TTS_MAX_CHARS", 20000)

    #: How many syntheses may run at the same time. Backpressure, not a quota.
    max_concurrency: int = _int("TTS_MAX_CONCURRENCY", 4)

    #: Number of rendered clips kept in memory so repeated playback is instant.
    cache_size: int = _int("TTS_CACHE_SIZE", 128)

    #: Seconds before the remote voice catalogue is fetched again.
    voice_cache_ttl: int = _int("TTS_VOICE_CACHE_TTL", 3600)


settings = Settings()
