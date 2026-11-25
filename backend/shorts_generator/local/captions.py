"""Build readable, clip-relative subtitle cues from Whisper word timing."""

from dataclasses import dataclass, replace
import math
from pathlib import Path

from ..edit_styles import STYLES


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return "".join(word.text for word in self.words).strip()


def _segment_words(segment: dict) -> list[dict]:
    if segment.get("words"):
        return segment["words"]
    # An occasional unaligned segment still gets readable caption groups.
    tokens = segment["text"].strip().split()
    if not tokens:
        return []
    step = (segment["end"] - segment["start"]) / len(tokens)
    return [
        {"start": segment["start"] + index * step,
         "end": segment["start"] + (index + 1) * step, "text": f" {token}"}
        for index, token in enumerate(tokens)
    ]


def build_cues(segments: list[dict], start: float, end: float, *, editing_style="balanced") -> list[Cue]:
    profile = STYLES[editing_style]
    words = []
    for segment in segments:
        if segment["end"] <= start or segment["start"] >= end:
            continue
        for word in _segment_words(segment):
            word_start, word_end = float(word["start"]), float(word["end"])
            if not (math.isfinite(word_start) and math.isfinite(word_end)
                    and word_end > start and word_start < end and word_end > word_start):
                continue
            text = "".join(char for char in word["text"] if char >= " " or char.isspace())
            text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
            if text.strip():
                words.append(Word(max(start, word_start) - start, min(end, word_end) - start, text))
    words.sort(key=lambda word: (word.start, word.end))

    groups: list[list[Word]] = []
    group: list[Word] = []
    for word in words:
        if group and (
            len(group) >= profile.caption_words or word.start - group[-1].end > 0.45
            or word.end - group[0].start > profile.caption_seconds
            or len("".join(token.text for token in group) + word.text) > profile.caption_chars
            or (len(group) >= 3 and group[-1].text.rstrip().endswith((".", "!", "?", "。", "؟", "！", "？")))
        ):
            groups.append(group)
            group = []
        group.append(word)
    if group:
        groups.append(group)

    cues = []
    for index, group in enumerate(groups):
        next_start = groups[index + 1][0].start if index + 1 < len(groups) else end - start
        stop = min(next_start, max(word.end for word in group) + 0.15)
        if stop - group[0].start >= 0.02:
            cues.append(Cue(group[0].start, stop, tuple(group)))
    return cues


def build_edited_cues(segments, source_start, pace, editing_style):
    offset = 0.0
    cues = []
    for left, right in pace.spans:
        for cue in build_cues(segments, source_start + left, source_start + right, editing_style=editing_style):
            words = tuple(replace(word, start=word.start + offset, end=word.end + offset) for word in cue.words)
            cues.append(Cue(cue.start + offset, cue.end + offset, words))
        offset += right - left
    return cues


def _timestamp(seconds: float, precision: int) -> tuple[int, int, int, int]:
    whole, fraction = divmod(round(max(0, seconds) * precision), precision)
    hours, remainder = divmod(whole, 3600)
    minutes, seconds = divmod(remainder, 60)
    return hours, minutes, seconds, fraction


def _srt_timestamp(seconds: float) -> str:
    hours, minutes, seconds, milliseconds = _timestamp(seconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def write_srt(path: Path, cues: list[Cue]) -> None:
    blocks = [
        f"{index}\n{_srt_timestamp(cue.start)} --> {_srt_timestamp(cue.end)}\n{cue.text}\n"
        for index, cue in enumerate(cues, 1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def _ass_timestamp(seconds: float) -> str:
    hours, minutes, seconds, centiseconds = _timestamp(seconds, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _ass_text(cue: Cue, style: str) -> str:
    # Subtitle text is untrusted. Prevent it from introducing ASS override tags.
    escapes = str.maketrans({"\\": "＼", "{": "｛", "}": "｝"})
    if style != "highlight":
        return cue.text.translate(escapes)

    chunks = []
    for index, word in enumerate(cue.words):
        stop = cue.words[index + 1].start if index + 1 < len(cue.words) else cue.end
        duration = max(0, round(stop * 100) - round(word.start * 100))
        text = word.text if index else word.text.lstrip()
        chunks.append(f"{{\\k{duration}}}{text.translate(escapes)}")
    return "".join(chunks)


def write_ass(path: Path, cues: list[Cue], style: str, width: int, height: int, *, editing_style="balanced") -> None:
    profile = STYLES[editing_style]
    primary = profile.accent if style == "highlight" else "&H00FFFFFF"
    size = max(12, round(min(height * 0.049, width * 0.082) * profile.caption_scale))
    outline = max(1, round(size * 0.10))
    margin_x = max(6, round(width * 0.075))
    margin_y = max(8, round(height * profile.caption_margin))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cutframe,Noto Sans,{size},{primary},&H00FFFFFF,&H00000000,&H50000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,{margin_x},{margin_x},{margin_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{_ass_timestamp(cue.start)},{_ass_timestamp(cue.end)},Cutframe,,0,0,0,,{_ass_text(cue, style)}\n"
        for cue in cues
    ]
    path.write_text(header + "".join(events), encoding="utf-8")
