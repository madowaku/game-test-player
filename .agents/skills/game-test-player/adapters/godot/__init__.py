"""Godot OS-window adapter used by the v0.1 black-box player."""

from .godot_adapter import (
    GodotAdapter,
    GodotAdapterError,
    ScreenshotEvidence,
    encode_bgra_png,
    normalize_key,
)

__all__ = [
    "GodotAdapter",
    "GodotAdapterError",
    "ScreenshotEvidence",
    "encode_bgra_png",
    "normalize_key",
]
