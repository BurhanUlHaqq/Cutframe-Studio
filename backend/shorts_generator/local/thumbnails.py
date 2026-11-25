"""Select a sharp, well-exposed cover from the clip's reframed footage."""

import math
from pathlib import Path

import cv2
import numpy as np

from .reframe import create_face_detector, detect_faces


def _resize(frame, max_edge):
    height, width = frame.shape[:2]
    if max(width, height) <= max_edge:
        return frame
    scale = max_edge / max(width, height)
    return cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))),
                      interpolation=cv2.INTER_AREA)


def _score(frame, detector):
    sample = _resize(frame, 360)
    height, width = sample.shape[:2]
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    exposure = 1 - abs(float(gray.mean()) - 128) / 128
    clipped = np.mean((gray < 12) | (gray > 246))
    contrast = min(1.0, float(gray.std()) / 55)
    detail = min(1.0, math.log1p(cv2.Laplacian(gray, cv2.CV_32F).var()) / math.log1p(650))
    score = exposure + contrast + detail - 1.8 * clipped

    if detector is not None and contrast >= 0.15:
        faces = detect_faces(cv2, detector, sample)
        visible = (
            min(1.0, math.sqrt(w * h / (width * height)) / 0.30)
            for x, y, w, h in faces
            if x > width * 0.025 and y > height * 0.025
            and x + w < width * 0.975 and y + h < height * 0.975
        )
        score += 1.25 * max(visible, default=0)
    return score


def write_thumbnail(video_path: str | Path, destination: Path, *, window=None) -> None:
    """Sample throughout the clip and write the best frame at up to 1920px."""
    video = cv2.VideoCapture(str(video_path))
    try:
        ok, first = video.read()
        if not ok:
            raise RuntimeError("Could not read a frame for the clip thumbnail.")
        count = max(1, int(video.get(cv2.CAP_PROP_FRAME_COUNT)))
        height, width = _resize(first, 360).shape[:2]
        try:
            detector = create_face_detector(cv2, (width, height))
        except cv2.error:
            detector = None
        start, last = 0, count - 1
        fps = video.get(cv2.CAP_PROP_FPS)
        if window and math.isfinite(fps) and fps > 0:
            start = min(last, round(window[0] * fps))
            last = min(last, max(start, math.ceil(window[1] * fps) - 1))
        positions = sorted({round(start + (last - start) * fraction)
                            for fraction in (0.06, 0.14, 0.23, 0.35, 0.50, 0.65, 0.80, 0.93)})
        best_frame, best_score = first, -math.inf
        for i in positions:
            if i == 0:
                frame = first
            else:
                video.set(cv2.CAP_PROP_POS_FRAMES, i)
                ok, frame = video.read()
                if not ok:
                    continue
            score = _score(frame, detector)
            if score > best_score:
                best_frame, best_score = frame, score
        ok, jpeg = cv2.imencode(".jpg", _resize(best_frame, 1920), [cv2.IMWRITE_JPEG_QUALITY, 94])
        if not ok:
            raise RuntimeError("Could not encode the clip thumbnail.")
        destination.write_bytes(jpeg.tobytes())
    finally:
        video.release()
