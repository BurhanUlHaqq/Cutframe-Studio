"""Find gameplay beats using sustained screen motion and audio changes."""

import math
import subprocess

import cv2
import numpy as np

from ..edit_styles import STYLES
from ..publication import publication_copy


def _audio_peaks(source, duration, step, count):
    # Decode at a low rate and keep only one RMS value per interval.
    result = np.zeros(count)
    try:
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-i", str(source),
            "-t", f"{duration:.4f}", "-map", "0:a:0", "-vn", "-ar", "8000", "-ac", "1", "-f", "s16le", "pipe:1",
        ]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            for index in range(count):
                data = process.stdout.read(2 * round(step * 8000))
                if not data:
                    break
                values = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
                result[index] = np.sqrt(np.mean(values ** 2))
    except OSError:
        pass
    return result


def _screen_frame(capture, time):
    capture.set(cv2.CAP_PROP_POS_MSEC, time * 1000)
    ok, frame = capture.read()
    if not ok:
        return None
    height, width = frame.shape[:2]
    # Put more weight on the game or demo area than on persistent corner overlays.
    center = frame[height // 9:height - height // 9, width // 8:width - width // 8]
    return cv2.cvtColor(cv2.resize(center, (128, 72), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)


def _motion(before, after):
    if before is None or after is None:
        return 0.0
    delta = after.astype(np.float32) - before
    return float(np.mean(np.abs(delta - np.median(delta))))


def select_gameplay(source, duration, has_audio, segments, clip_length, report):
    count = min(320, max(1, math.ceil(duration / 1.6)))
    step = duration / count
    audio = _audio_peaks(source, duration, step, count) if has_audio else np.zeros(count)
    samples = np.zeros(count)
    camera = cv2.VideoCapture(str(source))
    try:
        for index in range(count):
            time = step * (index + .5)
            spread = min(.45, step * .27)
            before, center, after = (_screen_frame(camera, at) for at in (time - spread, time, time + spread))
            incoming, outgoing = _motion(before, center), _motion(center, after)
            # A sustained change outranks a lone flash or one jump between menus.
            samples[index] = min(incoming, outgoing) * .76 + max(incoming, outgoing) * .24
            if index == count - 1 or index % max(1, count // 12) == 0:
                report("rank", 56 + 12 * (index + 1) // count,
                       f"Measuring gameplay activity · sample {index + 1} of {count}", event=False)
    finally:
        camera.release()

    if not np.any(samples >= .7):
        return {"highlights": []}
    sound_floor = float(np.median(audio))
    surge = np.clip((audio - sound_floor) / max(.025, np.percentile(audio, 90) - sound_floor), 0, 1)
    motion = np.clip(samples / max(5, np.percentile(samples, 90)), 0, 1.2)
    importance = .82 * motion + .18 * surge
    minimum, maximum = clip_length
    preferred = sum(STYLES["gameplay"].preferred_seconds) / 2
    length = min(duration, maximum or 180, max(minimum or 0, preferred))
    selected = []
    for index in sorted(range(count), key=lambda item: importance[item], reverse=True):
        if samples[index] < .7:
            continue
        peak = step * (index + .5)
        start = min(max(0.0, peak - length * .4), max(0.0, duration - length))
        end = start + length
        if any(min(end, clip["end_time"]) - max(start, clip["start_time"]) > length * .25 for clip in selected):
            continue
        moment = len(selected) + 1
        title = f"Gameplay highlight {moment}"
        excerpt = " ".join(segment["text"] for segment in segments if start <= segment["start"] < end)
        _, description = publication_copy({"title": title}, excerpt)
        selected.append({
            "start_time": round(start, 3), "end_time": round(end, 3), "title": title,
            "description": description or "A gameplay moment selected from the visible action.",
            "score": min(100, round(45 + 52 * importance[index])),
        })
    return {"highlights": selected}
