"""Parse and display source timestamps at millisecond precision."""

import re
from decimal import Decimal


def parse_source_timestamp(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{1,8}(?::[0-5]?[0-9]){0,2}(?:\.[0-9]{1,3})?", value):
        raise ValueError("Enter a time like 1:23:45, 12:30.500, or 75.")
    total = Decimal(0)
    for part in value.split(":"):
        total = total * 60 + Decimal(part)
    return int(total * 1000)


def source_timestamp(milliseconds):
    seconds, fraction = divmod(milliseconds, 1000)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    timecode = f"{hours}:{minute:02d}:{second:02d}" if hours else f"{minute}:{second:02d}"
    return f"{timecode}.{fraction:03d}".rstrip("0") if fraction else timecode
