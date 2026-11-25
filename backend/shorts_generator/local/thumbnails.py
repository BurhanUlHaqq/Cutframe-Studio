"""Find sharp, representative thumbnail frames and prepare clean alternatives."""

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .cover_design import CoverDesignError, design_cover, suggested_headline
from .reframe import create_face_detector, detect_faces


COVER_SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


@dataclass(frozen=True)
class CoverWindow:
    start: float
    end: float
    output_start: float = 0
    priority: float = 0


@dataclass(frozen=True)
class Candidate:
    index: int
    time: float
    quality: float
    signature: np.ndarray
    placement: str


def _resize(frame, max_edge):
    height, width = frame.shape[:2]
    if max(width, height) <= max_edge:
        return frame
    scale = max_edge / max(width, height)
    return cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))),
                      interpolation=cv2.INTER_AREA)


def _content(frame):
    """Remove uniform black letterbox bands from the assessment."""
    visible = np.max(frame, axis=2) > 17
    rows = np.flatnonzero(visible.mean(axis=1) > 0.10)
    cols = np.flatnonzero(visible.mean(axis=0) > 0.10)
    if len(rows) >= frame.shape[0] * 0.35 and len(cols) >= frame.shape[1] * 0.35:
        return frame[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    return frame


def _measure(frame, detector, main_screen):
    sample = _resize(frame, 384)
    height, width = sample.shape[:2]
    focus = sample[round(height * .30):round(height * .88)] if main_screen and height >= 8 else sample
    focus = _content(focus)
    gray = cv2.cvtColor(focus, cv2.COLOR_BGR2GRAY)
    low, median, high = np.percentile(gray, (10, 50, 90))
    contrast = min(1.0, (high - low) / 115)
    exposure = max(0, 1 - abs(float(median) - 125) / 125)
    clipping = float(np.mean((gray < 12) | (gray > 246)))
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    sharpness = min(1, math.log1p(float(laplacian.var())) / math.log1p(1900))
    # Reward recognizable structure at small sizes and penalize near-empty or blown-out frames.
    quality = 1.6 * sharpness + 1.1 * contrast + .75 * exposure - 1.45 * clipping
    if high - low < 15:
        quality -= .8
    signature = cv2.resize(sample, (20, 16), interpolation=cv2.INTER_AREA).astype(np.float32)
    top, bottom = np.array_split(cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY), 2)
    top_detail = float(cv2.Laplacian(top, cv2.CV_32F).var()) if top.size else 0
    bottom_detail = float(cv2.Laplacian(bottom, cv2.CV_32F).var()) if bottom.size else 0
    if detector is not None and contrast >= .15:
        faces = detect_faces(cv2, detector, sample)
        face_quality = 0.0
        for x, y, face_width, face_height in faces:
            area = face_width * face_height / (width * height)
            visible = x >= 0 and y >= 0 and x + face_width <= width and y + face_height <= height
            if visible:
                face_quality = max(face_quality, min(1.0, math.sqrt(area) / .32))
                if y + face_height / 2 < height / 2:
                    top_detail += 4000 * area
                else:
                    bottom_detail += 4000 * area
        quality += (.08 if main_screen else .42) * face_quality
    return quality, signature, "top" if main_screen is False and top_detail < .68 * bottom_detail else "bottom"


def _frame_at(video, index):
    video.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = video.read()
    return frame if ok else None


def _distance(left, right):
    return float(np.mean(np.abs(left.signature - right.signature)))


def _ranked_candidates(video, first, windows, limit, main_screen):
    fps = video.get(cv2.CAP_PROP_FPS)
    fps = fps if math.isfinite(fps) and fps > 0 else 30
    count = max(1, int(video.get(cv2.CAP_PROP_FRAME_COUNT)))
    height, width = _resize(first, 384).shape[:2]
    try:
        detector = create_face_detector(cv2, (width, height))
    except cv2.error:
        detector = None
    windows = windows or (CoverWindow(0, count / fps),)
    samples_per_window = 18 if len(windows) == 1 else 6
    candidates = []
    seen = set()
    for window in windows:
        start = max(0, min(count - 1, math.ceil(window.start * fps)))
        end = max(start, min(count - 1, math.ceil(window.end * fps) - 1))
        for fraction in np.linspace(.055, .945, samples_per_window):
            index = round(start + (end - start) * fraction)
            if index in seen:
                continue
            seen.add(index)
            frame = first if index == 0 else _frame_at(video, index)
            if frame is None:
                continue
            score, signature, placement = _measure(frame, detector, main_screen)
            time = window.output_start + max(0, index / fps - window.start)
            candidates.append(Candidate(index, time, score + window.priority, signature, placement))
    if not candidates:
        score, signature, placement = _measure(first, detector, main_screen)
        candidates = [Candidate(0, 0, score, signature, placement)]

    def representative(candidate):
        nearby = [_distance(candidate, other) for other in candidates if candidate.index != other.index]
        # A cluster of similar frames is more likely to show the subject than a fleeting transition.
        support = sum(distance < 19 for distance in nearby)
        return candidate.quality + .50 * support / max(1, len(nearby))

    ordered = sorted(candidates, key=representative, reverse=True)
    selected = [ordered[0]]
    for candidate in ordered[1:]:
        if len(selected) >= limit:
            break
        if candidate.quality >= ordered[0].quality - 1.20 and all(_distance(candidate, other) > 12 for other in selected):
            selected.append(candidate)
    return selected


def _canvas(frame, aspect_ratio):
    if not aspect_ratio:
        return _resize(frame, 1920)
    width, height = COVER_SIZES[aspect_ratio]
    source_height, source_width = frame.shape[:2]
    scale = min(width / source_width, height / source_height)
    size = (min(width, max(1, round(source_width * scale))), min(height, max(1, round(source_height * scale))))
    foreground = cv2.resize(frame, size, interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    if abs(size[0] - width) < 3 and abs(size[1] - height) < 3:
        return cv2.resize(foreground, (width, height)) if size != (width, height) else foreground
    backdrop = cv2.resize(frame, (48, 48), interpolation=cv2.INTER_AREA)
    backdrop = cv2.GaussianBlur(backdrop, (0, 0), 5)
    backdrop = cv2.resize(backdrop, (width, height), interpolation=cv2.INTER_LINEAR)
    backdrop = (backdrop * .42).astype(np.uint8)
    left, top = (width - size[0]) // 2, (height - size[1]) // 2
    backdrop[top:top + size[1], left:left + size[0]] = foreground
    return backdrop


def _write_frame(path, frame):
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 94, cv2.IMWRITE_JPEG_OPTIMIZE, 1])
    if not ok:
        raise RuntimeError("Could not encode the thumbnail.")
    path.write_bytes(jpeg.tobytes())


def create_thumbnail_set(video_path, destination, *, windows=(), aspect_ratio=None, main_screen=False, title=""):
    """Write a primary JPG and up to three clean, visually different choices."""
    video = cv2.VideoCapture(str(video_path))
    try:
        ok, first = video.read()
        if not ok:
            raise RuntimeError("Could not read a frame for the clip thumbnail.")
        candidates = _ranked_candidates(video, first, windows, 3, main_screen)
        options = []
        for candidate in candidates:
            frame = first if candidate.index == 0 else _frame_at(video, candidate.index)
            if frame is None:
                continue
            path = destination.with_name(f"{destination.stem}-frame-{len(options) + 1}.jpg")
            _write_frame(path, _canvas(frame, aspect_ratio))
            options.append({"path": str(path), "time": round(candidate.time, 2), "placement": candidate.placement})
        if not options:
            path = destination.with_name(f"{destination.stem}-frame-1.jpg")
            _write_frame(path, _canvas(first, aspect_ratio))
            options.append({"path": str(path), "time": 0, "placement": "bottom"})

        headline = suggested_headline(title)
        try:
            cover = design_cover(options[0]["path"], headline=headline, accent="gold", placement=options[0]["placement"])
        except CoverDesignError:
            headline = ""
            cover = Path(options[0]["path"]).read_bytes()
        destination.write_bytes(cover)
        return options, {"frame": 0, "headline": headline, "accent": "gold", "placement": "auto"}
    finally:
        video.release()


def write_thumbnail(video_path: str | Path, destination: Path, *, window=None):
    """Write a single clean cover for callers without a thumbnail editor."""
    video = cv2.VideoCapture(str(video_path))
    try:
        ok, first = video.read()
        if not ok:
            raise RuntimeError("Could not read a frame for the clip thumbnail.")
        windows = (CoverWindow(*window),) if window else ()
        best = _ranked_candidates(video, first, windows, 1, False)[0]
        frame = first if best.index == 0 else _frame_at(video, best.index)
        _write_frame(destination, _canvas(first if frame is None else frame, None))
    finally:
        video.release()
