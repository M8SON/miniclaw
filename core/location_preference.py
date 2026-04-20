from __future__ import annotations

from pathlib import Path

from core.memory_provider import MemoryProvider


def resolve_location(
    explicit_location: str = "",
    *,
    vault_path: Path | None = None,
    default: str = "",
) -> str:
    """Resolve location from explicit override, remembered memory, then default."""
    explicit = str(explicit_location or "").strip()
    if explicit:
        return explicit

    provider = MemoryProvider(vault_path=vault_path, backend="vault")
    remembered = provider.load_topic("location")
    if remembered:
        return remembered

    return str(default or "").strip()
