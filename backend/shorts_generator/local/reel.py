"""Encode selected excerpts once and assemble one seekable landscape video."""

import math
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

from .captions import Cue, build_cues, write_ass, write_srt
from .thumbnails import write_thumbnail


SIZES = {"480": (854, 480), "720": (1280, 720), "1080": (1920, 1080)}
FPS = 30


def encode_excerpt(source, folder, part, moment, duration, dimensions, has_audio, video_stream, captions, progress):
    width, height = dimensions
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps={FPS}"
    )
    if captions:
        video_filter += ",ass=segment.ass"
    # Padding the audio to the clip length keeps the video and audio clocks aligned.
    audio_filter = "apad"
    if duration >= 0.2:
        audio_filter += f",afade=t=in:st=0:d=0.04,afade=t=out:st={duration - 0.06:.6f}:d=0.06"
    command = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
        "-ss", f"{moment.start:.6f}", "-protocol_whitelist", "file", "-i", str(source.resolve()),
        "-t", f"{duration:.6f}", "-map", f"0:{video_stream}", "-vf", video_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        command.extend(["-map", "0:a:0", "-af", audio_filter, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"])
    with TemporaryFile() as errors, subprocess.Popen(
        [*command, part], cwd=folder, stdout=subprocess.PIPE, stderr=errors, text=True,
    ) as process:
        for line in process.stdout:
            if line.startswith("out_time_us="):
                value = line.strip().split("=", 1)[1]
                if value.lstrip("-").isdigit():
                    progress(min(duration, max(0, int(value) / 1_000_000)))
        if process.wait():
            errors.seek(0)
            raise subprocess.CalledProcessError(process.returncode, "FFmpeg highlight encoding", stderr=errors.read())


def chapter_timestamp(seconds):
    minutes, remainder = divmod(math.floor(seconds + 1e-6), 60)
    return f"{minutes:02d}:{remainder:02d}"


def youtube_chapter_text(chapters, duration):
    starts = [math.floor(chapter["time"] + 1e-6) for chapter in chapters]
    stops = [*starts[1:], math.floor(duration + 1e-6)]
    if len(chapters) < 3 or any(stop - start < 10 for start, stop in zip(starts, stops)):
        return ""
    return "\n".join(f"{chapter_timestamp(chapter['time'])} {chapter['title']}" for chapter in chapters)


def render_reel(source, work, moments, segments, quality, caption_style, has_audio, video_stream,
                title, description, strongest, report):
    dimensions = SIZES[quality]
    chapters, captions = [], []
    sizes = [max(1, math.floor(moment.duration * FPS + 1e-6)) for moment in moments]
    total_seconds = sum(sizes) / FPS
    frames_written = 0
    output = work.resolve() / "highlights.mp4"
    with TemporaryDirectory(prefix="reel-", dir=work) as staging:
        pending = work.resolve() / "highlights.pending.mp4"
        folder = Path(staging)
        concat = ["ffconcat version 1.0"]
        for index, (moment, frames) in enumerate(zip(moments, sizes), 1):
            duration = frames / FPS
            offset = frames_written / FPS
            part = f"moment-{index:02d}.mov"
            message = f"Encoding moment {index} of {len(moments)}"
            report("render", 70 + int(17 * offset / total_seconds), message)
            cues = build_cues(segments, moment.start, moment.start + duration) if caption_style != "off" else []
            if cues:
                write_ass(folder / "segment.ass", cues, caption_style, *dimensions)
                captions.extend(Cue(cue.start + offset, cue.end + offset, cue.words) for cue in cues)
            encode_excerpt(
                source, folder, part, moment, duration, dimensions, has_audio, video_stream, cues,
                lambda current: report("render", 70 + int(17 * (offset + current) / total_seconds),
                                       message, event=False),
            )
            concat.extend([f"file {part}", f"duration {duration:.6f}"])
            chapters.append({"time": offset, "source_time": moment.start,
                             "title": moment.title or f"Moment {index}"})
            frames_written += frames

        (folder / "reel.ffconcat").write_text("\n".join(concat) + "\n", encoding="ascii")
        report("render", 88, "Joining excerpts into the highlight reel")
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-f", "concat", "-safe", "1",
             "-i", "reel.ffconcat", "-t", f"{total_seconds:.6f}", "-c:v", "copy", "-c:a", "aac",
             "-b:a", "160k", "-movflags", "+faststart", str(pending)],
            cwd=folder, check=True, capture_output=True,
        )
        pending.replace(output)

    thumbnail = work / "highlights.jpg"
    try:
        write_thumbnail(source, thumbnail, window=(strongest.start, strongest.end))
    except RuntimeError:
        write_thumbnail(output, thumbnail)
    result = {
        "clip_url": str(output), "title": title, "description": description,
        "thumbnail_path": str(thumbnail), "score": None, "start_time": 0, "end_time": total_seconds,
        "chapters": chapters, "chapter_text": youtube_chapter_text(chapters, total_seconds),
    }
    if captions:
        track = work / "highlights.srt"
        write_srt(track, captions)
        result["caption_path"] = str(track)
    return result
