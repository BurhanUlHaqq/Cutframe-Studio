"""Tighten verified pauses and keep the picture, audio, and captions on one clock."""

import math
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Pace:
    duration: float
    removed: tuple[tuple[float, float], ...] = ()
    fps: Fraction = Fraction(30)

    @property
    def spans(self):
        return tuple(zip((0, *(end for _, end in self.removed)),
                         (*(start for start, _ in self.removed), self.duration)))

    @property
    def runtime(self):
        return self.duration - sum(end - start for start, end in self.removed)


def _speech_gaps(segments, start, duration):
    protected = []
    for segment in segments:
        if segment["end"] <= start or segment["start"] >= start + duration:
            continue
        for word in segment.get("words") or (segment,):
            if word["text"].strip() and word["end"] > word["start"]:
                left, right = max(0, word["start"] - start - .12), min(duration, word["end"] - start + .15)
                if right > left:
                    protected.append((left, right))
    previous = 0.0
    for left, right in sorted(protected):
        if left > previous > 0:
            yield previous, left
        previous = max(previous, right)


def _silent_ranges(source, start, duration, trigger):
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-ss", f"{start:.6f}",
        "-protocol_whitelist", "file", "-i", str(source), "-t", f"{duration:.6f}", "-map", "0:a:0",
        "-vn", "-af", f"silencedetect=noise=-36dB:d={max(.26, trigger - .35):.3f}", "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=True, encoding="utf-8", errors="replace")
    silent, left = [], None
    for event, value in re.findall(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)", result.stderr):
        point = min(duration, max(0.0, float(value)))
        if event == "start":
            left = point
        elif left is not None:
            silent.append((left, point))
            left = None
    if left is not None:
        silent.append((left, duration))
    return silent


def _fps(source):
    output = subprocess.check_output([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "V:0",
        "-show_entries", "stream=avg_frame_rate", "-of", "default=nw=1:nk=1", str(source),
    ], timeout=15, text=True)
    fps = Fraction(output.strip())
    return min(fps, Fraction(60)) if fps >= 12 else Fraction(30)


def select_pauses(segments, start, end, quiet, profile, minimum, fps):
    """Intersect transcript gaps and silence, retaining speech handles on both sides."""
    duration = end - start
    budget = min(duration * profile.max_trim_ratio, max(0.0, duration - max(1.0, minimum or 0)))
    candidates = []
    for gap_start, gap_end in _speech_gaps(segments, start, duration):
        if gap_end - gap_start < profile.pause_trigger:
            continue
        for quiet_start, quiet_end in quiet:
            left, right = max(gap_start, quiet_start), min(gap_end, quiet_end)
            if right - left >= profile.pause_trigger - .27:
                candidates.append((left, right))
    removable = []
    for left, right in sorted(candidates, key=lambda span: span[1] - span[0], reverse=True):
        # Keep part of the original breath on each side, even with a tight budget.
        length = min(budget, right - left - profile.pause_keep)
        if length < .22:
            continue
        center = (left + right) / 2
        a = math.ceil((center - length / 2) * fps) / float(fps)
        b = math.floor((center + length / 2) * fps) / float(fps)
        if b - a >= .20:
            removable.append((a, b))
            budget -= b - a
        if len(removable) >= 12 or budget < .22:
            break
    return Pace(duration, tuple(sorted(removable)), fps)


def plan_pacing(source, segments, start, end, profile, minimum):
    duration = end - start
    if (not profile.pause_trigger or duration < 2 or (minimum and duration - minimum < .25)
            or not any(right - left >= profile.pause_trigger for left, right in _speech_gaps(segments, start, duration))):
        return Pace(duration)
    try:
        quiet = _silent_ranges(source, start, duration, profile.pause_trigger)
        return select_pauses(segments, start, end, quiet, profile, minimum, _fps(source)) if quiet else Pace(duration)
    except (subprocess.SubprocessError, ValueError, ZeroDivisionError):
        return Pace(duration)


def tighten_clip(source, destination, pace):
    """Drop video frames at exact boundaries and concatenate matching PCM spans."""
    spans = pace.spans
    fps = pace.fps
    expressions = []
    for index, (left, right) in enumerate(spans):
        first = round(left * fps)
        stop = round(right * fps)
        selection = f"gte(n,{first})" if index == len(spans) - 1 else f"between(n,{first},{stop - 1})"
        expressions.append(selection)
    video = f"[0:v:0]fps={fps}:start_time=0,select='{'+'.join(expressions)}',setpts=N/(({fps})*TB)[video]"
    split = "[0:a:0]aresample=48000,asetpts=PTS-STARTPTS,asplit=" + str(len(spans))
    split += "".join(f"[source{index}]" for index in range(len(spans)))
    audio = [split]
    for index, (left, right) in enumerate(spans):
        length = right - left
        fade = min(.012, length / 4)
        audio.append(
            f"[source{index}]atrim=start={left:.8f}:end={right:.8f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={fade:.6f},afade=t=out:st={length - fade:.8f}:d={fade:.6f}[keep{index}]"
        )
    audio.append("".join(f"[keep{index}]" for index in range(len(spans))) + f"concat=n={len(spans)}:v=0:a=1[audio]")
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source), "-filter_complex", ";".join([video, *audio]),
        "-map", "[video]", "-map", "[audio]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14",
        "-c:a", "pcm_s16le", str(destination),
    ], check=True, capture_output=True)
