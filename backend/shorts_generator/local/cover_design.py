"""Compose a readable cover over a real video frame."""

import io
import unicodedata
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ACCENTS = {"white": "#FFFFFF", "gold": "#FFDE6B", "mint": "#9DF1C8"}
FONT_ROOT = Path("/usr/share/fonts/truetype/noto")
FONT_EXTENSIONS = {
    "arabic": "NotoSansArabic-Bold.ttf", "devanagari": "NotoSansDevanagari-Bold.ttf",
    "bengali": "NotoSansBengali-Bold.ttf", "hebrew": "NotoSansHebrew-Bold.ttf",
}


class CoverDesignError(ValueError):
    pass


def clean_headline(value):
    if not isinstance(value, str):
        return ""
    return " ".join("".join(char for char in value if not unicodedata.category(char).startswith("C")
                            or char.isspace()).split()).strip()


def suggested_headline(title):
    title = clean_headline(title).split(" | ", 1)[0]
    if any(char.isalpha() for char in title) and 5 <= len(title) <= 38 and len(title.split()) <= 6:
        if title.casefold() not in {"video highlights", "highlight reel"} and "_" not in title:
            return title
    return ""


def font_name(text):
    for char in text:
        if char.isalpha():
            name = unicodedata.name(char, "").split(" ", 1)[0].lower()
            if name in FONT_EXTENSIONS:
                return str(FONT_ROOT / FONT_EXTENSIONS[name])
            if name in {"cjk", "hiragana", "katakana", "hangul"}:
                suffix = "JP" if name in {"hiragana", "katakana"} else "KR" if name == "hangul" else "SC"
                return f"{suffix}:/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    return str(FONT_ROOT / "NotoSans-Bold.ttf")


@lru_cache(maxsize=40)
def font_at(name, size):
    if ":" in name and name[:2] in {"JP", "KR", "SC"}:
        language, name = name.split(":", 1)
        index = {"JP": 0, "KR": 1, "SC": 2}[language]
    else:
        index = 0
    for filename in (name, "DejaVuSans-Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(filename, size, index=index if filename == name else 0)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw, text, font, maximum):
    if draw.textlength(text, font=font) <= maximum:
        return text
    breaks = [index for index, char in enumerate(text) if char == " " or
              unicodedata.east_asian_width(char) in {"W", "F"}]
    middle = sorted(breaks, key=lambda index: abs(len(text) / 2 - index))
    for index in middle:
        split = index if text[index] == " " else index + 1
        left, right = text[:split].strip(), text[split:].strip()
        if left and right and max(draw.textlength(line, font=font) for line in (left, right)) <= maximum:
            return left + "\n" + right
    return ""


def design_cover(source, *, headline="", accent="white", placement="bottom"):
    """Return the exact JPG bytes used by both preview and export."""
    headline = clean_headline(headline)
    if not headline:
        return Path(source).read_bytes()
    with Image.open(source) as original:
        image = original.convert("RGB")
    width, height = image.size
    margin_x = round(width * 0.09)
    margin_y = round(height * (0.105 if width < height else 0.095))
    draw = ImageDraw.Draw(image)
    name = font_name(headline)
    base = min(width, height)
    for factor in (0.122, 0.106, 0.094, 0.083, 0.073, 0.064, 0.055):
        font = font_at(name, max(12, round(base * factor)))
        wrapped = _wrap(draw, headline, font, width - 2 * margin_x)
        if wrapped:
            break
    else:
        raise CoverDesignError("Shorten the headline so it fits on two lines.")

    spacing = max(4, round(base * 0.014))
    stroke = max(2, round(base * 0.005))
    left, top, right, bottom = draw.multiline_textbbox(
        (0, 0), wrapped, font=font, spacing=spacing, align="center", stroke_width=stroke,
    )
    text_height = bottom - top
    at_top = placement == "top"
    edge = margin_y
    fade_depth = min(height, edge + text_height + round(height * 0.20))
    distance = np.arange(height) if at_top else np.arange(height)[::-1]
    opacity = (np.clip(1 - distance / fade_depth, 0, 1) ** 1.4 * 225).astype(np.uint8)
    shade = Image.fromarray(opacity.reshape(height, 1)).resize((width, height))
    image.paste((13, 11, 22), (0, 0, width, height), shade)
    y = edge if at_top else height - edge - text_height
    draw.multiline_text(((width - right + left) / 2 - left, y - top), wrapped, font=font,
                        spacing=spacing, align="center", fill=ACCENTS[accent],
                        stroke_width=stroke, stroke_fill="#100d18")
    output = io.BytesIO()
    image.save(output, "JPEG", quality=93, optimize=True)
    return output.getvalue()
