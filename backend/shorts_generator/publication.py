"""Prepare post copy from model output and the speech inside the selected cut."""

import re
import unicodedata


TITLE_LENGTH = 60
DESCRIPTION_LENGTH = 300
SENTENCE = re.compile(r".+?(?:[.!?]+(?=\s|$)|[。！？؟।]+|$)")
PLACEHOLDERS = {
    "clip", "highlight", "short", "untitled", "untitled highlight",
    "viral clip", "viral moment", "video clip", "an interesting moment",
}


def _plain_text(value, label=""):
    if not isinstance(value, str):
        return ""
    text = "".join(char for char in value if unicodedata.category(char) != "Cc" or char.isspace())
    text = unicodedata.normalize("NFC", " ".join(text.split())).strip("*_` ")
    if label:
        text = re.sub(rf"^(?:#{1,6}\s+)?{label}\s*[*_`]*\s*:\s*", "", text, flags=re.IGNORECASE)
    return text.strip("*_` ")


def _shorten(text, limit):
    if len(text) <= limit:
        return text
    prefix = text[:limit - 1]
    boundary = prefix.rfind(" ")
    if boundary >= limit * 0.65 and text[limit - 1] != " ":
        prefix = prefix[:boundary]
    # Keep an accent attached to its letter when the cut falls inside a word.
    while prefix and unicodedata.category(text[len(prefix)]).startswith("M"):
        prefix = prefix[:-1]
    return prefix.rstrip(" ,;:-") + "…"


def _comparison(text):
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def publication_copy(highlight, speech=""):
    """Return a concise title and description, using clipped speech as a fallback."""
    speech = _plain_text(speech) or _plain_text(highlight.get("hook_sentence"))
    sentences = [sentence.strip() for sentence in SENTENCE.findall(speech)]
    opening = sentences[0] if sentences else ""

    title = _plain_text(highlight.get("title"), "title")
    title_key = _comparison(title)
    if (not title_key or title_key in PLACEHOLDERS
            or re.fullmatch(r"(?:clip|short|highlight) \d+", title_key)):
        title = opening.rstrip(".。")
    title_key = _comparison(title)
    title = _shorten(title, TITLE_LENGTH)

    description = _plain_text(highlight.get("description"), "description")
    description_key = _comparison(description)
    if not description_key or description_key in {title_key, _comparison(title)} | PLACEHOLDERS:
        excerpt = sentences[:2]
        if len(excerpt) > 1 and _comparison(excerpt[0].rstrip(".。")) == _comparison(title):
            excerpt = excerpt[1:]
        description = " ".join(excerpt)
    return title, _shorten(description, DESCRIPTION_LENGTH)
