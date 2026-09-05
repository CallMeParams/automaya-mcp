"""Synthetic test images built with Pillow for the craft tests."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def gradient(w: int = 200, h: int = 100) -> Image.Image:
    """Left black to right white ramp."""
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for x in range(w):
        v = int(round(255 * x / (w - 1)))
        draw.line([(x, 0), (x, h - 1)], fill=(v, v, v))
    return img


def overexposed(w: int = 160, h: int = 90) -> Image.Image:
    """Dark grey frame with a pure white block over the left half."""
    img = Image.new("RGB", (w, h), (20, 20, 20))
    ImageDraw.Draw(img).rectangle([0, 0, w // 2, h], fill=(255, 255, 255))
    return img


def dark(w: int = 160, h: int = 90) -> Image.Image:
    return Image.new("RGB", (w, h), (3, 3, 3))


def horizon(w: int = 160, h: int = 90, split: float = 2.0 / 3.0) -> Image.Image:
    """Blue sky over green ground with the boundary at ``split`` of the height."""
    img = Image.new("RGB", (w, h), (120, 160, 220))
    ImageDraw.Draw(img).rectangle([0, int(h * split), w, h], fill=(60, 80, 40))
    return img


def noisy(w: int = 160, h: int = 90) -> Image.Image:
    """Mid grey checkerboard: lots of edges, no clipping."""
    img = Image.new("RGB", (w, h), (110, 110, 110))
    draw = ImageDraw.Draw(img)
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            if (x // 4 + y // 4) % 2 == 0:
                draw.rectangle([x, y, x + 3, y + 3], fill=(150, 150, 150))
    return img


def save(img: Image.Image, folder: Path, name: str) -> str:
    path = folder / name
    img.save(path)
    return str(path)
