"""Cut, reframe, and export short video clips locally."""
import math
import os
import subprocess
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Dict, List, Optional

from ..control import RenderCancelled
from ..edit_styles import STYLES
from ..publication import speech_for_description
from .captions import Cue, build_edited_cues, write_ass, write_srt
from .media import cut_subclip
from .pacing import Pace, plan_pacing, tighten_clip
from .reframe import Framer
from .thumbnails import create_thumbnail_set, write_thumbnail

def _ratio(aspect_ratio: str) -> float:
    """Parse '9:16' → 9/16, '1:1' → 1.0."""
    try:
        w, h = aspect_ratio.split(":")
        ratio = float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return 9.0 / 16.0
    return ratio if 0 < ratio < float("inf") else 9.0 / 16.0


def _render_frames(
    in_path: str, silent_path: str, aspect_ratio: str, framing: str,
    on_frames: Optional[Callable[[int], None]] = None,
    *,
    editing_style="balanced",
) -> tuple[int, int]:
    """Apply the selected framing to a silent video stream."""
    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "opencv-python-headless is required to reframe videos. Install it with:\n"
            "    pip install -r requirements.txt"
        ) from e

    target_ratio = _ratio(aspect_ratio)
    cap = cv2.VideoCapture(in_path)
    writer = None
    try:
        if not cap.isOpened():
            raise RuntimeError(f"could not open {in_path}")

        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if not math.isfinite(fps) or fps <= 0:
            fps = 30.0
        if src_w < 2 or src_h < 2:
            raise RuntimeError(f"Invalid video frame size: {in_path}")

        # Compute the largest crop that fits inside the frame at the target ratio.
        if target_ratio < src_w / src_h:
            crop_h = src_h
            crop_w = int(crop_h * target_ratio)
        else:
            crop_w = src_w
            crop_h = int(crop_w / target_ratio)
        crop_w = max(2, crop_w - (crop_w % 2))
        crop_h = max(2, crop_h - (crop_h % 2))
        ratio = Fraction(target_ratio).limit_denominator(1000)
        width_step, height_step = 2 * ratio.numerator, 2 * ratio.denominator
        if width_step:
            multiplier = min(src_w // width_step, src_h // height_step)
            if multiplier:
                crop_w, crop_h = width_step * multiplier, height_step * multiplier
        full_frame = framing == "stream" or (editing_style == "tutorial" and framing == "fit")
        if full_frame and aspect_ratio == "9:16" and src_w >= src_h * 1.25 and src_h >= 32:
            # The full-width screen can use a larger portrait canvas than a subject crop.
            multiplier = min(src_h, 1080) // width_step
            crop_w, crop_h = width_step * multiplier, height_step * multiplier

        framer = Framer(cv2, (src_w, src_h), (crop_w, crop_h), aspect_ratio, framing, fps,
                        steady=STYLES[editing_style].steady, inset_only=editing_style == "gameplay")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(silent_path, fourcc, fps, (crop_w, crop_h))
        if not writer.isOpened():
            raise RuntimeError(f"could not write {silent_path}")

        index, last_percent = 0, 0
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            writer.write(framer.frame(frame, index))
            index += 1
            if on_frames is not None and count > 0:
                percent = min(100, int(100 * index / count))
                if percent >= last_percent + 5:
                    on_frames(percent)
                    last_percent = percent
        if not index:
            raise RuntimeError(f"No video frames in {in_path}")
        return crop_w, crop_h
    finally:
        cap.release()
        if writer is not None:
            writer.release()


def _encode_clip(cut_path: str, silent_path: str, out_path: str, cue_dir: Path, burn_captions: bool,
                 *, audio_filter="") -> None:
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-i", silent_path,
        "-i", cut_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-map", "0:v:0", "-map", "1:a:0?",
        "-shortest",
    ]
    if audio_filter:
        cmd.extend(("-af", audio_filter + ",aresample=48000,apad", "-c:a", "aac", "-b:a", "160k"))
    else:
        cmd.extend(("-c:a", "copy"))
    if burn_captions:
        cmd.extend(("-vf", "ass=captions.ass"))
    subprocess.run([*cmd, out_path], cwd=cue_dir, check=True)


def crop_clip_local(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
    *,
    framing: str = "auto",
    cues: Optional[list[Cue]] = None,
    caption_style: str = "classic",
    thumbnail_path: Optional[Path] = None,
    on_thumbnail: Optional[Callable[[str], None]] = None,
    on_activity: Optional[Callable[[str, float, bool], None]] = None,
    editing_style: str = "balanced",
    pace: Optional[Pace] = None,
    already_cut: bool = False,
) -> str:
    """Cut + reframe one highlight, returning the local mp4 path."""
    out_path = str(Path(out_path).resolve())
    with TemporaryDirectory(prefix=".clip-", dir=Path(out_path).parent) as directory:
        path = Path(directory)
        cut_path = source_path if already_cut else str(path / "cut.mp4")
        silent_path = str(path / "silent.mp4")
        if not already_cut:
            if on_activity is not None:
                on_activity("Cutting the source segment", 0, True)
            cut_subclip(source_path, start_time, end_time, cut_path)
        if pace and pace.removed:
            if on_activity is not None:
                on_activity(f"Tightening {len(pace.removed)} verified pauses", 0.10, True)
            paced_path = str(path / "paced.mkv")
            tighten_clip(cut_path, paced_path, pace)
            cut_path = paced_path
        on_frames = None
        if on_activity is not None:
            on_activity("Reframing video", 0.15, True)
            on_frames = lambda percent: on_activity(f"Reframing video · {percent}%", 0.15 + 0.60 * percent / 100, False)
        width, height = _render_frames(cut_path, silent_path, aspect_ratio, framing, on_frames,
                                       editing_style=editing_style)
        burn_captions = bool(cues) and caption_style != "off"
        if burn_captions:
            write_ass(path / "captions.ass", cues, caption_style, width, height, editing_style=editing_style)
        pending = path / "final.mp4"
        if on_activity is not None:
            on_activity("Encoding video with captions" if burn_captions else "Encoding video", 0.75, True)
        _encode_clip(cut_path, silent_path, str(pending), path, burn_captions,
                     audio_filter=STYLES[editing_style].audio_filter)
        if thumbnail_path is not None or on_thumbnail:
            if on_activity is not None:
                on_activity("Preparing thumbnail choices", 0.95, True)
            if on_thumbnail:
                on_thumbnail(silent_path)
            else:
                cover = path / "cover.jpg"
                write_thumbnail(silent_path, cover)
                cover.replace(thumbnail_path)
        pending.replace(out_path)
    return out_path


def crop_highlights_local(
    source_path: str,
    highlights: List[Dict],
    *,
    out_dir: str,
    aspect_ratio: str = "9:16",
    framing: str = "auto",
    caption_style: str = "off",
    segments: Optional[list[dict]] = None,
    on_clip: Optional[Callable[[int, int], None]] = None,
    on_activity: Optional[Callable[[int, int, str, float, bool], None]] = None,
    editing_style: str = "balanced",
    minimum_duration: Optional[int] = None,
    exact: bool = False,
) -> List[Dict]:
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    results: List[Dict] = []
    profile = STYLES[editing_style]
    framing = profile.framing if framing == "auto" else framing
    for i, h in enumerate(highlights, 1):
        out_path = os.path.join(out_dir, f"short_{i:02d}.mp4")
        print(f"[clip/local] {i}/{len(highlights)}: {h.get('title', '(untitled)')}", flush=True)
        try:
            start, end = float(h["start_time"]), float(h["end_time"])
            pace = Pace(end - start) if exact else plan_pacing(source_path, segments or [], start, end, profile, minimum_duration)
            cues = build_edited_cues(segments or [], start, pace, editing_style) if caption_style != "off" else []
            thumbnail_path = Path(out_path).with_suffix(".jpg")
            result = {**h, "clip_url": out_path, "thumbnail_path": str(thumbnail_path)}
            if segments:
                result["description_context"] = speech_for_description(segments, start, end)
            if pace.removed:
                result.update(duration_seconds=round(pace.runtime, 3), pauses_trimmed=len(pace.removed))

            def prepare_cover(frame_video):
                options, design = create_thumbnail_set(
                    frame_video, thumbnail_path, aspect_ratio=aspect_ratio,
                    title=h.get("cover_headline") or h.get("title"),
                    main_screen=framing == "stream" and aspect_ratio == "9:16",
                )
                result.update(thumbnail_options=options, thumbnail_design=design)

            update = None
            if on_activity is not None:
                update = lambda message, portion, event: on_activity(i, len(highlights), message, portion, event)
            crop_clip_local(
                source_path, start, end, aspect_ratio, out_path, framing=framing,
                cues=cues, caption_style=caption_style, on_thumbnail=prepare_cover,
                on_activity=update, editing_style=editing_style, pace=pace, already_cut=exact,
            )
            if cues:
                caption_path = Path(out_path).with_suffix(".srt")
                write_srt(caption_path, cues)
                result["caption_path"] = str(caption_path)
            results.append(result)
        except RenderCancelled:
            raise
        except Exception as e:
            print(f"[clip/local] {i} failed: {e}", flush=True)
            results.append({**h, "clip_url": None, "error": str(e)})
        if on_clip is not None:
            on_clip(i, len(highlights))
    return results
