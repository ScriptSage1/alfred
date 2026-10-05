"""Windows-only helpers: a system-wide hotkey, and bringing a window to the front.

The hotkey uses the Win32 RegisterHotKey API through ctypes (no extra package).
Windows itself watches for the key combination and tells Alfred when it is
pressed, no matter which application has focus (VS Code, a browser, ...).
Unlike keyboard-hook libraries, nothing here sees your other keystrokes.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
_HOTKEY_ID = 1

_MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)},  # virtual-key codes = ASCII
    **{str(d): ord(str(d)) for d in range(10)},
    **{f"f{n}": 0x6F + n for n in range(1, 13)},                     # F1 = 0x70
}


class HotkeyError(Exception):
    """The hotkey could not be parsed or registered."""


def parse_hotkey(text: str) -> tuple[int, int]:
    """'ctrl+alt+space' -> (MOD_CONTROL | MOD_ALT, VK_SPACE)."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise HotkeyError("empty hotkey")
    *mods, key = parts
    modifiers = 0
    for mod in mods:
        if mod not in _MODIFIERS:
            raise HotkeyError(f"unknown modifier {mod!r} in {text!r} (use ctrl, alt, shift, win)")
        modifiers |= _MODIFIERS[mod]
    if key not in _KEYS:
        raise HotkeyError(f"unsupported key {key!r} in {text!r}")
    if not modifiers:
        raise HotkeyError(f"{text!r} needs at least one modifier, e.g. ctrl+alt+{key}")
    return modifiers, _KEYS[key]


class GlobalHotkey:
    """Calls `callback` (on a background thread) each time the hotkey is pressed.

    RegisterHotKey ties the hotkey to the thread that registered it, and
    Windows delivers WM_HOTKEY to that thread's message queue. So we run a
    small dedicated thread that registers the key and then waits for messages.
    """

    def __init__(self, hotkey: str, callback: Callable[[], None]) -> None:
        self.hotkey = hotkey
        self._modifiers, self._vk = parse_hotkey(hotkey)
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: str | None = None

    def start(self) -> None:
        if sys.platform != "win32":
            raise HotkeyError("global hotkeys are only implemented for Windows")
        self._thread = threading.Thread(target=self._run, name="alfred-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._error:
            raise HotkeyError(self._error)
        log.info("global hotkey %s registered", self.hotkey)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            _user32().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=5)

    def _run(self) -> None:
        from ctypes import wintypes

        user32 = _user32()
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, _HOTKEY_ID, self._modifiers | MOD_NOREPEAT, self._vk):
            error = ctypes.get_last_error()
            self._error = (
                f"{self.hotkey} is already used by another program (or another Alfred). "
                'Choose a different one with hotkey = "..." in the [ui] section of alfred.toml.'
                if error == 1409  # ERROR_HOTKEY_ALREADY_REGISTERED
                else f"could not register {self.hotkey} (Windows error {error})"
            )
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        try:
            # GetMessageW blocks until a message arrives; it returns 0 for WM_QUIT.
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    try:
                        self._callback()
                    except Exception:
                        log.exception("hotkey callback failed")
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)


def bring_to_front(hwnd: int) -> None:
    """Give keyboard focus to `hwnd`, even if another program is in front.

    Windows only lets the foreground program hand over focus. Briefly joining
    its input queue (AttachThreadInput) is the standard, documented workaround.
    """
    if sys.platform != "win32":
        return
    user32 = _user32()
    foreground = user32.GetForegroundWindow()
    if foreground == hwnd:
        return
    this_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    other_thread = user32.GetWindowThreadProcessId(foreground, None)
    attached = other_thread and other_thread != this_thread and user32.AttachThreadInput(
        other_thread, this_thread, True
    )
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(other_thread, this_thread, False)


@functools.cache
def _user32():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    return user32
