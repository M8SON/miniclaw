"""ALSA device resolution for the voice pipeline.

Both PyAudio and sounddevice address devices by integer index, but the
underlying ALSA card numbers shift across reboots based on USB enumeration
order. Resolving by case-insensitive name substring keeps the same physical
device selected regardless of the index it gets assigned.

Defaults match the bundled hardware: ``Array`` for the reSpeaker XVF3800 and
``KT USB`` for the Creative Pebble's USB DAC. Override per-deployment with the
``MIC_DEVICE`` and ``SPEAKER_DEVICE`` environment variables.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_MIC_NAME = "Array"
DEFAULT_SPEAKER_NAME = "KT USB"


def _mic_query() -> str:
    return os.environ.get("MIC_DEVICE", DEFAULT_MIC_NAME)


def _speaker_query() -> str:
    return os.environ.get("SPEAKER_DEVICE", DEFAULT_SPEAKER_NAME)


def resolve_input_device(query: str | None = None) -> int | None:
    """Return the PyAudio input device index whose name contains ``query``.

    Returns ``None`` if PortAudio is unavailable or no input device matches,
    in which case callers should fall back to the host default.
    """
    name = (query or _mic_query()).lower()
    try:
        import pyaudio
    except ImportError:
        return None

    audio = pyaudio.PyAudio()
    try:
        for i in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0 and name in info.get("name", "").lower():
                logger.info("Mic device %d: %s", i, info["name"])
                return i
    finally:
        audio.terminate()

    logger.warning("No input device matched %r — using PortAudio default", name)
    return None


def resolve_output_device(query: str | None = None) -> int | None:
    """Return the sounddevice output index whose name contains ``query``.

    Returns ``None`` if sounddevice is unavailable or no output device matches.
    """
    name = (query or _speaker_query()).lower()
    try:
        import sounddevice as sd
    except ImportError:
        return None

    for i, info in enumerate(sd.query_devices()):
        if info.get("max_output_channels", 0) > 0 and name in info.get("name", "").lower():
            logger.info("Speaker device %d: %s", i, info["name"])
            return i

    logger.warning("No output device matched %r — using sounddevice default", name)
    return None
