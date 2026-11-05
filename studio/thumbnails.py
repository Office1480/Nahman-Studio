"""Miniature : l'image de la vidéo avec le texte choisi en surimpression."""
import io
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

FONTS = ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
SIZE = (1280, 720)


def base_image(video_id: str) -> bytes:
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        response = httpx.get(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", timeout=20)
        if response.status_code == 200:
            return response.content
    raise RuntimeError("Image de la vidéo introuvable")


def _font(size: int):
    path = next((p for p in FONTS if Path(p).exists()), None)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    return lines + [line] if line else lines


def compose(image: bytes, text: str) -> bytes:
    canvas = ImageOps.fit(Image.open(io.BytesIO(image)).convert("RGB"), SIZE)
    shade = Image.new("L", (1, SIZE[1]))
    for y in range(SIZE[1]):
        shade.putpixel((0, y), int(max(0, (y - SIZE[1] * 0.35) / (SIZE[1] * 0.65)) * 225))
    canvas.paste(Image.new("RGB", SIZE, (12, 14, 28)), mask=shade.resize(SIZE))

    draw = ImageDraw.Draw(canvas)
    for size in (112, 96, 84, 72, 60):
        font = _font(size)
        lines = _wrap(draw, text.upper(), font, SIZE[0] - 120)
        if len(lines) <= 3:
            break
    y = SIZE[1] - 50 - len(lines) * int(size * 1.15)
    for line in lines:
        draw.text((60, y), line, font=font, fill=(255, 214, 92), stroke_width=4, stroke_fill=(0, 0, 0))
        y += int(size * 1.15)
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


def make(video_id: str, text: str) -> bytes:
    return compose(base_image(video_id), text)
