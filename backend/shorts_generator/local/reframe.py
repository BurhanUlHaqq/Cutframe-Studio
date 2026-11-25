"""Scene-aware local framing for talking heads, interviews, and wide material."""

import math
from pathlib import Path

import numpy as np


MODEL_PATH = Path(__file__).with_name("models") / "face_detection_yunet_2026may.onnx"


def create_face_detector(cv, source_size, *, max_edge=480):
    scale = min(1.0, max_edge / max(source_size))
    size = tuple(max(32, round(length * scale / 32) * 32) for length in source_size)
    return cv.FaceDetectorYN.create(str(MODEL_PATH), "", size, 0.80)


def detect_faces(cv, detector, frame):
    """Return face boxes as (x, y, width, height) in original frame pixels."""
    width, height = detector.getInputSize()
    sample = cv.resize(frame, (width, height), interpolation=cv.INTER_AREA)
    _, faces = detector.detect(sample)
    if faces is None:
        return []
    sx, sy = frame.shape[1] / width, frame.shape[0] / height
    return [(x * sx, y * sy, w * sx, h * sy) for x, y, w, h in faces[:, :4]]


def choose_targets(faces, width, allow_split, previous):
    """Decide whether a single, split, or full-frame shot can show the subjects."""
    if not faces or len(faces) > 2:
        return "fit", ()

    if allow_split and len(faces) == 2:
        left, right = sorted(faces, key=lambda face: face[0])
        if (right[0] - left[0] >= width * 0.28
                and min(left[2], right[2]) >= max(left[2], right[2]) * 0.30):
            return "split", ((left[0], left[1]), (right[0], right[1]))

    if previous is not None:
        largest = max(face[2] for face in faces)
        eligible = [face for face in faces if face[2] >= largest * 0.55]
        subject = min(eligible, key=lambda face: math.dist(face[:2], previous))
    else:
        subject = max(faces, key=lambda face: face[2])
    return "track", ((subject[0], subject[1]),)


def webcam_candidates(boxes, width, height):
    """Find small, off-center faces typical of a webcam inset."""
    return [
        (x, y, w, h) for x, y, w, h in boxes
        if 0.035 <= h / height <= 0.30 and w <= width * 0.20
        and abs((x + w / 2) / width - 0.5) >= 0.22
        and (h <= height * 0.16 or abs((y + h / 2) / height - 0.5) >= 0.14)
    ]


class Framer:
    def __init__(self, cv, source_size, output_size, aspect_ratio, mode, fps, *, steady=False, inset_only=False):
        self.cv = cv
        self.source_w, self.source_h = source_size
        self.width, self.height = output_size
        self.center = self.source_w / 2, self.source_h / 2
        self.mode = mode
        self.detector = None
        self.stride = max(1, round(fps / 6))
        self.smoothing = 1 - math.exp(-(3 if steady else 5) / fps)
        self.layout_confirmations = 5 if steady else 3
        self.inset_only = inset_only
        self.track_webcam = mode == "stream" or (mode == "auto" and not steady)
        self.previous_sample = None
        self.layout = None
        self.pending = None
        self.pending_count = 0
        self.targets = ()
        self.centers = ()
        self.allow_split = aspect_ratio == "9:16" and self.source_w >= self.source_h * 1.25
        self.webcam_candidate = None
        self.webcam_count = 0
        self.webcam = None
        self.webcam_target = None
        self.webcam_misses = 0
        if mode == "auto" or (mode == "stream" and self.allow_split):
            self.detector = create_face_detector(cv, source_size, max_edge=640 if self.allow_split else 480)

    def _observe(self, boxes, cut):
        previous = self.centers[0] if self.layout == "track" and not cut else None
        faces = [(x + w / 2, y + h / 2, w * h) for x, y, w, h in boxes]
        layout, targets = choose_targets(
            faces, self.source_w, self.allow_split, previous,
        )
        if layout == "fit" and abs(self.source_w / self.source_h - self.width / self.height) < 0.03:
            layout, targets = "track", (self.center,)
        if cut or self.layout is None:
            self.layout, self.targets, self.centers = layout, targets, targets
            self.pending, self.pending_count = None, 0
        elif layout == self.layout:
            self.targets = targets
            self.pending, self.pending_count = None, 0
        else:
            self.pending_count = self.pending_count + 1 if self.pending == layout else 1
            self.pending = layout
            if self.pending_count >= self.layout_confirmations:
                self.layout, self.targets, self.centers = layout, targets, targets
                self.pending, self.pending_count = None, 0

    def _observe_webcam(self, boxes, cut):
        candidates = webcam_candidates(boxes, self.source_w, self.source_h)
        if self.mode == "stream" and not self.inset_only and not candidates and len(boxes) == 1:
            candidates = boxes
        if self.mode == "auto" and self.webcam is None and len(candidates) > 1:
            areas = sorted((box[2] * box[3] for box in candidates), reverse=True)
            if areas[1] >= areas[0] * 0.65:
                candidates = []
        previous = self.webcam_target or self.webcam_candidate
        nearby = [
            box for box in candidates if previous
            and abs((box[0] + box[2] / 2) - (previous[0] + previous[2] / 2)) < self.source_w * 0.075
            and abs((box[1] + box[3] / 2) - (previous[1] + previous[3] / 2)) < self.source_h * 0.12
        ]
        if not nearby and self.webcam is not None:
            self.webcam_misses += 1
            if not cut and self.webcam_misses < 4:
                return
            self.webcam = self.webcam_target = None
        self.webcam_misses = 0
        if not candidates:
            self.webcam_candidate, self.webcam_count = None, 0
            return
        candidate = max(nearby or candidates, key=lambda box: box[2] * box[3])
        self.webcam_candidate = candidate
        self.webcam_count = self.webcam_count + 1 if nearby else 1
        if (self.mode == "stream" and not self.inset_only) or self.webcam_count >= 3:
            self.webcam_target = candidate
            if self.webcam is None:
                self.webcam = candidate

    def _cover(self, frame, width, height, center, *, crop_height=None):
        scale = min(self.source_w / width, self.source_h / height)
        if crop_height is not None:
            scale = min(scale, crop_height / height)
        crop_w, crop_h = max(1, round(width * scale)), max(1, round(height * scale))
        x = max(0, min(self.source_w - crop_w, round(center[0] - crop_w / 2)))
        y = max(0, min(self.source_h - crop_h, round(center[1] - crop_h / 2)))
        crop = frame[y:y + crop_h, x:x + crop_w]
        return self.cv.resize(crop, (width, height), interpolation=self.cv.INTER_LINEAR)

    def _background(self, frame):
        background = self._cover(frame, max(4, self.width // 14), max(4, self.height // 14), self.center)
        background = self.cv.GaussianBlur(background, (0, 0), 2)
        return self.cv.resize(background, (self.width, self.height), interpolation=self.cv.INTER_LINEAR)

    def _fit(self, frame):
        base = self.cv.convertScaleAbs(self._background(frame), alpha=0.55)
        scale = min(self.width / self.source_w, self.height / self.source_h)
        width, height = max(1, round(self.source_w * scale)), max(1, round(self.source_h * scale))
        x, y = (self.width - width) // 2, (self.height - height) // 2
        base[y:y + height, x:x + width] = self.cv.resize(frame, (width, height), interpolation=self.cv.INTER_AREA)
        return base

    def _stream(self, frame):
        if self.webcam is None:
            return self._fit(frame)
        self.webcam = tuple(
            value + (target - value) * self.smoothing
            for value, target in zip(self.webcam, self.webcam_target)
        )
        base = self.cv.convertScaleAbs(self._background(frame), alpha=0.27, beta=7)
        screen_height = max(1, round(self.width * self.source_h / self.source_w))
        screen_top = (self.height - screen_height) // 2
        base[screen_top:screen_top + screen_height] = self.cv.resize(
            frame, (self.width, screen_height), interpolation=self.cv.INTER_AREA,
        )
        camera_width = max(1, round(self.width * 0.72))
        camera_height = max(1, min(round(self.height * 0.23), screen_top - max(1, self.height // 20)))
        camera_x, camera_y = (self.width - camera_width) // 2, (screen_top - camera_height) // 2
        x, y, w, h = self.webcam
        base[camera_y:camera_y + camera_height, camera_x:camera_x + camera_width] = self._cover(
            frame, camera_width, camera_height, (x + w * 0.5, y + h * 0.72), crop_height=2.15 * h,
        )
        return base

    def frame(self, frame, index):
        if self.mode == "center":
            return self._cover(frame, self.width, self.height, self.center)
        if self.mode == "fit" or (self.mode == "stream" and not self.allow_split):
            return self._fit(frame)

        sample = self.cv.resize(frame, (32, 18), interpolation=self.cv.INTER_AREA)
        cut = self.previous_sample is not None and self.cv.absdiff(sample, self.previous_sample).mean() > 44
        self.previous_sample = sample
        if cut or index % self.stride == 0:
            boxes = detect_faces(self.cv, self.detector, frame)
            if self.allow_split and self.track_webcam:
                self._observe_webcam(boxes, cut)
            if self.mode == "auto":
                self._observe(boxes, cut)

        if self.mode == "stream" or self.webcam is not None:
            return self._stream(frame)
        if self.webcam_candidate is not None:
            # Keep the screen visible while a potential webcam inset is being confirmed.
            return self._fit(frame)

        self.centers = tuple(
            (x + (tx - x) * self.smoothing, y + (ty - y) * self.smoothing)
            for (x, y), (tx, ty) in zip(self.centers, self.targets)
        )
        if self.layout == "split":
            top = self.height // 2
            return np.concatenate((
                self._cover(frame, self.width, top, self.centers[0]),
                self._cover(frame, self.width, self.height - top, self.centers[1]),
            ))
        if self.layout == "track":
            return self._cover(frame, self.width, self.height, self.centers[0])
        return self._fit(frame)
