"""Operate a Godot game through its visible Windows window.

This module deliberately has no Godot project or engine introspection API.  It
launches a packaged executable (or passes a project directory to Godot), finds
the resulting top-level window, captures pixels, and sends ordinary keyboard
and mouse events.  That boundary is what makes the default first_time mode a
black-box playtest.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time
from typing import Sequence
import zlib


class GodotAdapterError(RuntimeError):
    """Raised when the OS adapter cannot perform a requested operation."""


class WindowNotFoundError(GodotAdapterError):
    """Raised when the game process has no visible window yet."""


@dataclass(frozen=True, slots=True)
class ScreenshotEvidence:
    """Metadata returned after a screenshot is written."""

    path: Path
    width: int
    height: int
    captured_at: str
    window_title: str

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "captured_at": self.captured_at,
            "window_title": self.window_title,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _safe_label(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return (value or "observation")[:60]


def encode_bgra_png(pixels: bytes, width: int, height: int, stride: int | None = None) -> bytes:
    """Encode a top-down BGRA buffer as a PNG without third-party packages."""

    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    row_stride = stride or width * 4
    if row_stride < width * 4 or len(pixels) < row_stride * height:
        raise ValueError("pixel buffer is smaller than the requested image")

    # PNG color type 6 is RGBA.  GDI's DIB is BGRA, so convert while building
    # scanlines.  A filter byte of zero keeps the encoder deterministic.
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        row_start = y * row_stride
        for x in range(width):
            offset = row_start + x * 4
            blue, green, red, _alpha = pixels[offset : offset + 4]
            scanlines.extend((red, green, blue, 255))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        + chunk(b"IEND", b"")
    )


_VK_CODES: dict[str, int] = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "CLEAR": 0x0C,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "SHIFT": 0x10,
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "ALT": 0x12,
    "PAUSE": 0x13,
    "CAPSLOCK": 0x14,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PGUP": 0x21,
    "PAGEDOWN": 0x22,
    "PGDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PRINTSCREEN": 0x2C,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "NUMLOCK": 0x90,
    "SCROLLLOCK": 0x91,
    "LWIN": 0x5B,
    "RWIN": 0x5C,
}
_VK_CODES.update({chr(code): code for code in range(ord("A"), ord("Z") + 1)})
_VK_CODES.update({chr(code): code for code in range(ord("0"), ord("9") + 1)})
for _function_key in range(1, 13):
    _VK_CODES[f"F{_function_key}"] = 0x6F + _function_key


def normalize_key(key: str | int) -> int:
    """Convert a common player-facing key name to a Windows virtual key."""

    if isinstance(key, int):
        if 0 <= key <= 0xFF:
            return key
        raise ValueError("integer key codes must be between 0 and 255")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("key must be a non-empty name")
    normalized = key.strip().upper().replace("-", "_")
    normalized = re.sub(r"^(KEY|VK)_", "", normalized)
    if normalized in _VK_CODES:
        return _VK_CODES[normalized]
    if len(normalized) == 1:
        raise ValueError(f"unsupported key: {key!r}")
    raise ValueError(
        f"unsupported key {key!r}; use a letter, digit, function key, arrow, or common name"
    )


if os.name == "nt":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class _RGBQUAD(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", wintypes.BYTE),
            ("rgbGreen", wintypes.BYTE),
            ("rgbRed", wintypes.BYTE),
            ("rgbReserved", wintypes.BYTE),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]

    _user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.SetFocus.argtypes = [wintypes.HWND]
    _user32.SetFocus.restype = wintypes.HWND
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    try:
        _user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        _user32.SwitchToThisWindow.restype = None
    except AttributeError:
        # Present on supported desktop Windows versions, but optional for
        # restricted compatibility environments.
        pass
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
    _user32.PrintWindow.restype = wintypes.BOOL
    _user32.GetWindowDC.argtypes = [wintypes.HWND]
    _user32.GetWindowDC.restype = ctypes.c_void_p
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = ctypes.c_void_p
    _user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.SetCursorPos.restype = wintypes.BOOL
    _user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
    _user32.keybd_event.restype = None
    _user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _user32.mouse_event.restype = None
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.ScreenToClient.restype = wintypes.BOOL

    _gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    _gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    _gdi32.CreateDIBSection.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _gdi32.CreateDIBSection.restype = ctypes.c_void_p
    _gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _gdi32.SelectObject.restype = ctypes.c_void_p
    _gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    _gdi32.DeleteObject.restype = wintypes.BOOL
    _gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    _gdi32.DeleteDC.restype = wintypes.BOOL
    _gdi32.BitBlt.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    _gdi32.BitBlt.restype = wintypes.BOOL

    _SW_RESTORE = 9
    _PW_RENDERFULLCONTENT = 2
    _DIB_RGB_COLORS = 0
    _BI_RGB = 0
    _SRCCOPY = 0x00CC0020
    _CAPTUREBLT = 0x40000000
    _KEYEVENTF_KEYUP = 0x0002
    _MOUSEEVENTF_MOVE = 0x0001
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004
    _MOUSEEVENTF_RIGHTDOWN = 0x0008
    _MOUSEEVENTF_RIGHTUP = 0x0010
    _MOUSEEVENTF_MIDDLEDOWN = 0x0020
    _MOUSEEVENTF_MIDDLEUP = 0x0040
    _WM_MOUSEMOVE = 0x0200
    _WM_LBUTTONDOWN = 0x0201
    _WM_LBUTTONUP = 0x0202
    _WM_LBUTTONDBLCLK = 0x0203
    _WM_RBUTTONDOWN = 0x0204
    _WM_RBUTTONUP = 0x0205
    _WM_MBUTTONDOWN = 0x0207
    _WM_MBUTTONUP = 0x0208
    _MK_LBUTTON = 0x0001
    _MK_RBUTTON = 0x0002
    _MK_MBUTTON = 0x0010


class GodotAdapter:
    """Launch and control one Godot game without reading its internals."""

    def __init__(
        self,
        game: str | os.PathLike[str] | Sequence[str],
        *,
        godot_binary: str | os.PathLike[str] | None = None,
        game_args: Sequence[str] = (),
        window_title: str | None = None,
        evidence_dir: str | os.PathLike[str] = "evidence",
        cwd: str | os.PathLike[str] | None = None,
        startup_wait: float = 1.0,
        window_timeout: float = 15.0,
    ) -> None:
        self.game = game
        self.godot_binary = str(godot_binary) if godot_binary else None
        self.game_args = [str(arg) for arg in game_args]
        self.window_title = window_title.strip() if window_title else None
        self.evidence_dir = Path(evidence_dir).expanduser().resolve()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.startup_wait = self._nonnegative_number(startup_wait, "startup_wait")
        self.window_timeout = self._nonnegative_number(window_timeout, "window_timeout")
        self.command, inferred_cwd = self._build_command()
        self.cwd = Path(cwd).expanduser().resolve() if cwd else inferred_cwd
        if self.cwd and not self.cwd.is_dir():
            raise GodotAdapterError(f"working directory does not exist: {self.cwd}")
        self._process: subprocess.Popen[bytes] | None = None
        self._window_hwnd: int | None = None
        self._held_keys: set[int] = set()
        self._capture_index = 0

    @staticmethod
    def _nonnegative_number(value: float, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a number") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{name} must be a finite non-negative number")
        return number

    def _resolve_godot_binary(self) -> str:
        candidate = self.godot_binary or os.environ.get("GODOT_BIN")
        if candidate:
            resolved = shutil.which(candidate) or candidate
            if not Path(resolved).exists() and not shutil.which(candidate):
                raise GodotAdapterError(
                    f"Godot binary was not found: {candidate}. Pass --godot or set GODOT_BIN."
                )
            return str(resolved)
        for name in ("godot", "godot4"):
            found = shutil.which(name)
            if found:
                return found
        raise GodotAdapterError(
            "No Godot executable found. Pass --godot <path> or set GODOT_BIN when using a project directory."
        )

    def _build_command(self) -> tuple[list[str], Path | None]:
        if isinstance(self.game, (list, tuple)):
            if not self.game:
                raise ValueError("game command cannot be empty")
            command = [str(item) for item in self.game] + self.game_args
            return command, None

        raw_game = str(self.game)
        game_path = Path(raw_game).expanduser().resolve()
        if game_path.is_dir():
            binary = self._resolve_godot_binary()
            return [binary, "--path", str(game_path), *self.game_args], game_path
        if game_path.is_file():
            if game_path.suffix.lower() != ".exe":
                raise GodotAdapterError(
                    f"Game file must be a packaged .exe or a project directory: {game_path}"
                )
            return [str(game_path), *self.game_args], game_path.parent

        command_name = shutil.which(raw_game)
        if command_name:
            return [command_name, *self.game_args], None
        # A command string is intentionally not shell-expanded.  This prevents
        # a first-time run from accidentally executing a project-provided shell
        # command; pass a list to the Python API for a custom executable.
        raise GodotAdapterError(
            f"Game target does not exist: {raw_game}. Pass a .exe path or project directory."
        )

    @staticmethod
    def _require_windows() -> None:
        if os.name != "nt":
            raise GodotAdapterError(
                "Godot OS-window adapter v0.1 currently requires Windows; add a platform backend for this host."
            )

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def launch_game(self) -> dict[str, object]:
        """Start the game and wait until a visible top-level window appears."""

        self._require_windows()
        if self.is_running:
            self.terminate_game()
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self._process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd) if self.cwd else None,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        self._window_hwnd = None
        if self.startup_wait:
            time.sleep(self.startup_wait)
        deadline = time.monotonic() + self.window_timeout
        while time.monotonic() <= deadline:
            if self._process.poll() is not None:
                code = self._process.returncode
                self._process = None
                raise GodotAdapterError(f"game exited before its window appeared (exit code {code})")
            try:
                hwnd = self._resolve_window()
            except WindowNotFoundError:
                time.sleep(0.1)
                continue
            self._activate_window(hwnd)
            return {
                "pid": self.pid,
                "command": list(self.command),
                "window_title": self._window_title(hwnd),
                "started_at": _utc_now(),
            }
        self.terminate_game()
        raise WindowNotFoundError(
            "Game process started but no visible window was found. Pass --window-title if it has multiple windows."
        )

    def _window_title(self, hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(max(length + 1, 1))
        _user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _resolve_window(self) -> int:
        self._require_windows()
        if self._window_hwnd and _user32.IsWindow(self._window_hwnd):
            if not self.window_title or self.window_title.lower() in self._window_title(self._window_hwnd).lower():
                return self._window_hwnd

        pid = self.pid
        if pid is None and not self.window_title:
            raise WindowNotFoundError("launch_game must be called before targeting a window")
        candidates: list[tuple[int, str, int]] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd: int, _lparam: int) -> bool:
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return True
            title = self._window_title(hwnd)
            if self.window_title and self.window_title.lower() not in title.lower():
                return True
            window_pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if pid is not None and window_pid.value != pid:
                return True
            rect = _RECT()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            if area:
                candidates.append((int(hwnd), title, area))
            return True

        _user32.EnumWindows(callback, 0)
        if not candidates:
            raise WindowNotFoundError("no visible game window found")
        candidates.sort(key=lambda item: item[2], reverse=True)
        self._window_hwnd = candidates[0][0]
        return self._window_hwnd

    @staticmethod
    def _activate_window(hwnd: int) -> None:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, _SW_RESTORE)
        # A child process can otherwise fail to receive its first key event
        # when another app remains the foreground window. Temporarily joining
        # the caller to the foreground input queue lets Windows apply the same
        # focus a player click would establish, without sending gameplay input.
        foreground = _user32.GetForegroundWindow()
        current_thread = _kernel32.GetCurrentThreadId()
        foreground_thread = _user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = bool(
            foreground_thread
            and current_thread != foreground_thread
            and _user32.AttachThreadInput(current_thread, foreground_thread, True)
        )
        try:
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
            _user32.SetFocus(hwnd)
            switch_to_window = getattr(_user32, "SwitchToThisWindow", None)
            if switch_to_window is not None:
                switch_to_window(hwnd, True)
        finally:
            if attached:
                _user32.AttachThreadInput(current_thread, foreground_thread, False)

    @staticmethod
    def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
        rect = _RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise GodotAdapterError("could not read the game window bounds")
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise GodotAdapterError("game window has no visible area")
        return rect.left, rect.top, width, height

    def _capture_window(self, hwnd: int, width: int, height: int) -> bytes:
        window_dc = _user32.GetWindowDC(hwnd)
        if not window_dc:
            raise GodotAdapterError("GetWindowDC failed")
        memory_dc = None
        bitmap = None
        old_bitmap = None
        bits = ctypes.c_void_p()
        try:
            memory_dc = _gdi32.CreateCompatibleDC(window_dc)
            if not memory_dc:
                raise GodotAdapterError("CreateCompatibleDC failed")
            info = _BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            # A negative height creates a top-down DIB, matching PNG scanlines.
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = _BI_RGB
            bitmap = _gdi32.CreateDIBSection(
                window_dc,
                ctypes.byref(info),
                _DIB_RGB_COLORS,
                ctypes.byref(bits),
                None,
                0,
            )
            if not bitmap or not bits.value:
                raise GodotAdapterError("CreateDIBSection failed")
            old_bitmap = _gdi32.SelectObject(memory_dc, bitmap)
            rendered = bool(_user32.PrintWindow(hwnd, memory_dc, _PW_RENDERFULLCONTENT))
            if not rendered:
                left, top, _width, _height = self._window_rect(hwnd)
                screen_dc = _user32.GetDC(None)
                try:
                    rendered = bool(
                        _gdi32.BitBlt(
                            memory_dc,
                            0,
                            0,
                            width,
                            height,
                            screen_dc,
                            left,
                            top,
                            _SRCCOPY | _CAPTUREBLT,
                        )
                    )
                finally:
                    _user32.ReleaseDC(None, screen_dc)
            if not rendered:
                raise GodotAdapterError("PrintWindow and BitBlt could not capture the game")
            return ctypes.string_at(bits.value, width * height * 4)
        finally:
            if old_bitmap and memory_dc:
                _gdi32.SelectObject(memory_dc, old_bitmap)
            if bitmap:
                _gdi32.DeleteObject(bitmap)
            if memory_dc:
                _gdi32.DeleteDC(memory_dc)
            _user32.ReleaseDC(hwnd, window_dc)

    def capture_screenshot(self, *, step: int | None = None, label: str = "observation") -> ScreenshotEvidence:
        """Capture the visible game window and return a durable evidence path."""

        self._require_windows()
        hwnd = self._resolve_window()
        self._activate_window(hwnd)
        _left, _top, width, height = self._window_rect(hwnd)
        pixels = self._capture_window(hwnd, width, height)
        self._capture_index += 1
        step_label = f"{step:04d}" if step is not None else f"{self._capture_index:04d}"
        filename = f"{step_label}-{_safe_label(label)}.png"
        path = self.evidence_dir / filename
        path.write_bytes(encode_bgra_png(pixels, width, height))
        return ScreenshotEvidence(path, width, height, _utc_now(), self._window_title(hwnd))

    def key_down(self, key: str | int) -> None:
        """Hold one ordinary keyboard key down."""

        self._require_windows()
        hwnd = self._resolve_window()
        self._activate_window(hwnd)
        vk = normalize_key(key)
        _user32.keybd_event(vk, 0, 0, 0)
        self._held_keys.add(vk)

    def key_up(self, key: str | int) -> None:
        """Release one ordinary keyboard key."""

        self._require_windows()
        vk = normalize_key(key)
        _user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        self._held_keys.discard(vk)

    def press_key(self, key: str | int, *, hold_s: float = 0.05) -> None:
        """Press and release one key; no key sequence is generated."""

        duration = self._nonnegative_number(hold_s, "hold_s")
        self.key_down(key)
        try:
            if duration:
                time.sleep(duration)
        finally:
            self.key_up(key)

    def mouse_move(self, x: int, y: int, *, relative: bool = True) -> None:
        """Move the cursor, using game-window-relative coordinates by default."""

        self._require_windows()
        hwnd = self._resolve_window()
        self._activate_window(hwnd)
        target_x, target_y = int(x), int(y)
        if relative:
            left, top, _width, _height = self._window_rect(hwnd)
            target_x += left
            target_y += top
        if not _user32.SetCursorPos(target_x, target_y):
            raise GodotAdapterError("SetCursorPos failed")

    def mouse_click(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
        interval_s: float = 0.08,
        relative: bool = True,
    ) -> None:
        """Click at a visible location in the game window."""

        if clicks < 1 or clicks > 20:
            raise ValueError("clicks must be between 1 and 20")
        interval = self._nonnegative_number(interval_s, "interval_s")
        self._require_windows()
        hwnd = self._resolve_window()
        self._activate_window(hwnd)
        self.mouse_move(x, y, relative=relative)
        flags = {
            "left": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
            "right": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
            "middle": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
        }
        normalized_button = button.strip().lower()
        if normalized_button not in flags:
            raise ValueError("button must be left, right, or middle")
        down, up = flags[normalized_button]
        # Godot Control nodes can ignore legacy mouse_event injection when the
        # runner is attached to a different Windows input desktop.  Deliver the
        # same ordinary click through the target window's client messages after
        # moving the real cursor; this remains OS-level and does not inspect the
        # scene, nodes, or game state.
        _message_flags = {
            "left": (_WM_LBUTTONDOWN, _WM_LBUTTONUP, _MK_LBUTTON),
            "right": (_WM_RBUTTONDOWN, _WM_RBUTTONUP, _MK_RBUTTON),
            "middle": (_WM_MBUTTONDOWN, _WM_MBUTTONUP, _MK_MBUTTON),
        }
        message_down, message_up, message_mask = _message_flags[normalized_button]
        point = wintypes.POINT(int(x), int(y))
        if not relative:
            if not _user32.ScreenToClient(hwnd, ctypes.byref(point)):
                raise GodotAdapterError("ScreenToClient failed")
        # lParam packs signed 16-bit client coordinates.  The mask preserves
        # the Windows message representation for points near the origin.
        lparam = ((int(point.y) & 0xFFFF) << 16) | (int(point.x) & 0xFFFF)
        for index in range(clicks):
            if not _user32.PostMessageW(hwnd, _WM_MOUSEMOVE, 0, lparam):
                raise GodotAdapterError("PostMessageW(mouse move) failed")
            if not _user32.PostMessageW(hwnd, message_down, message_mask, lparam):
                raise GodotAdapterError("PostMessageW(mouse down) failed")
            if not _user32.PostMessageW(hwnd, message_up, 0, lparam):
                raise GodotAdapterError("PostMessageW(mouse up) failed")
            if index + 1 < clicks and interval:
                time.sleep(interval)

    def wait(self, seconds: float) -> None:
        """Wait for a player-visible state transition."""

        duration = self._nonnegative_number(seconds, "seconds")
        time.sleep(duration)

    def restart_game(self) -> dict[str, object]:
        """Terminate and relaunch the same black-box target."""

        self.terminate_game()
        return self.launch_game()

    def terminate_game(self) -> None:
        """Release held keys and stop only the process launched by this adapter."""

        if os.name == "nt":
            for vk in list(self._held_keys):
                _user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        self._held_keys.clear()
        process = self._process
        self._process = None
        self._window_hwnd = None
        if process is None:
            return
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                # The process object is intentionally not replaced or reused;
                # a caller can report that termination was not confirmed.
                return

    def __enter__(self) -> "GodotAdapter":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.terminate_game()


__all__ = [
    "GodotAdapter",
    "GodotAdapterError",
    "ScreenshotEvidence",
    "WindowNotFoundError",
    "encode_bgra_png",
    "normalize_key",
]
