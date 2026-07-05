from __future__ import annotations

import ctypes
import io
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NativeWindow:
    hwnd: int
    title: str
    rect: tuple[int, int, int, int]

    def model(self) -> dict[str, Any]:
        left, top, right, bottom = self.rect
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "left": left,
            "top": top,
            "width": max(0, right - left),
            "height": max(0, bottom - top),
        }


def _user32() -> Any:
    return ctypes.windll.user32  # type: ignore[attr-defined]


def capture_available() -> bool:
    try:
        import PIL.ImageGrab  # noqa: F401

        return hasattr(ctypes, "windll")
    except Exception:
        return False


def list_windows(title_contains: str | None = None) -> list[dict[str, Any]]:
    if not hasattr(ctypes, "windll"):
        return []
    user32 = _user32()
    windows: list[NativeWindow] = []

    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        if title_contains and title_contains.lower() not in title.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return True
        windows.append(NativeWindow(hwnd=int(hwnd), title=title, rect=(rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(enum_proc_type(callback), 0)
    return [window.model() for window in windows]


def _placeholder(text: str, width: int = 1280, height: int = 720) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (17, 20, 23))
    draw = ImageDraw.Draw(image)
    lines = [
        "AFSIM Native Display",
        text,
        "Open Warlock or Mystic from the web console, then refresh this panel.",
    ]
    y = height // 2 - 44
    for index, line in enumerate(lines):
        fill = (232, 237, 242) if index == 0 else (150, 162, 174)
        draw.text((48, y + index * 32), line, fill=fill)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=82)
    return output.getvalue()


def capture_window_jpeg(title_contains: str = "Warlock") -> bytes:
    if not capture_available():
        return _placeholder("Pillow ImageGrab is not available on this Python environment.")
    from PIL import ImageGrab

    candidates = list_windows(title_contains)
    if not candidates:
        return _placeholder(f"No visible native window matched title: {title_contains}")
    window = candidates[0]
    left = int(window["left"])
    top = int(window["top"])
    right = left + int(window["width"])
    bottom = top + int(window["height"])
    try:
        image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    except TypeError:
        image = ImageGrab.grab(bbox=(left, top, right, bottom))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=78)
    return output.getvalue()


def native_display_status(stream_url: str = "") -> dict[str, Any]:
    return {
        "stream_url": stream_url,
        "capture_available": capture_available(),
        "windows": {
            "warlock": list_windows("Warlock"),
            "mystic": list_windows("Mystic"),
        },
        "updated_at": time.time(),
    }
