"""Ingest, transcribe, rank, and render shorts on the local machine."""
import subprocess
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from typing import Callable, Dict, List, Optional

from .highlights import get_highlights
from .local.clipper import crop_highlights_local
from .local.downloader import cached_youtube_source, download_youtube_local, download_youtube_section
from .local.gameplay import select_gameplay
from .local.llm import PROVIDERS, RankingConfig, ranking_client, ranking_config
from .local.media import InvalidSourceVideo, cut_subclip, resolve_source_range, validate_local_video
from .local.transcriber import transcribe_local
from .timestamps import source_timestamp


class NoMatchingHighlights(ValueError):
    pass


def _clock(seconds):
    minutes, remainder = divmod(round(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def progress_reporter(on_progress):
    def report(stage, percent, message, event=True):
        if on_progress is not None:
            on_progress(stage, percent, message, event=event)
    return report


def prepare_source(youtube_url, uploaded_video, download_format, out_dir, report):
    if uploaded_video is not None:
        report("download", 5, "Checking the uploaded video and audio tracks")
        return str(uploaded_video)

    report("download", 5, "Fetching source information from YouTube")

    def download_progress(received, total):
        amount = f"{received / 1024**2:.1f} MB"
        if total:
            amount += f" of {total / 1024**2:.1f} MB"
        report("download", 5 + min(20, 20 * received // total) if total else 5,
               f"Receiving a source stream · {amount}", event=False)

    return download_youtube_local(
        youtube_url, fmt=download_format, out_dir=out_dir, on_bytes=download_progress,
        on_cache=lambda: report("download", 25, "Reusing the saved YouTube download"),
    )


def prepare_bounded_source(youtube_url, uploaded_video, quality, source_range, out_dir, report):
    local_source = uploaded_video if uploaded_video is not None else cached_youtube_source(youtube_url, out_dir)
    if local_source is not None:
        return prepare_source_range(local_source, source_range, out_dir, report)
    report("download", 5, "Finding the selected section on YouTube")
    return download_youtube_section(
        youtube_url, out_dir=out_dir, fmt=quality, source_range=source_range,
        on_progress=lambda received, portion: report(
            "download", 5 + int(20 * portion),
            f"Downloading selected section · {received / 1024**2:.1f} MB received", event=False,
        ),
    )


def prepare_source_range(source, source_range, out_dir, report):
    source = Path(source)
    duration, _, _ = validate_local_video(source, require_audio=False)
    start_ms, end_ms = source_range
    start, end = resolve_source_range(duration, start_ms, end_ms)
    stamp = source.stat()
    directory = Path(out_dir)
    bounded = directory / f"range-{start_ms}-{end_ms}-{stamp.st_size:x}-{stamp.st_mtime_ns:x}.mp4"
    label = f"{source_timestamp(start_ms)}–{source_timestamp(end_ms)}"
    if bounded.is_file():
        report("download", 25, f"Reusing the saved source range · {label}")
        return str(bounded), start, end
    directory.mkdir(parents=True, exist_ok=True)
    report("download", 25, f"Cutting the source range · {label}")
    with TemporaryDirectory(prefix=".range-", dir=directory) as temporary:
        pending = Path(temporary) / "range.mp4"
        try:
            cut_subclip(str(source), start, end, str(pending))
            validate_local_video(pending, require_audio=False)
        except (subprocess.CalledProcessError, InvalidSourceVideo) as exc:
            raise InvalidSourceVideo("Could not cut a playable source range. Check the timestamps or choose a longer range.") from exc
        pending.replace(bounded)
    return str(bounded), start, end


def transcribe_source(source_path, language, out_dir, caption_style, whisper_model, report, *, transcription=None):
    report("transcribe", 28, "Preparing audio and the local Whisper model")
    return transcribe_local(
        source_path, language=language, out_dir=out_dir,
        allow_legacy=caption_style == "off" and not whisper_model, whisper_model=whisper_model,
        **(transcription or {}),
        on_cache=lambda: report("transcribe", 54, "Reusing the saved transcript"),
        on_progress=lambda done, total: report(
            "transcribe", 29 + int(24 * done / total),
            f"Transcribing speech · {_clock(done)} of {_clock(total)}", event=False,
        ),
    )


def render_timestamp_clip(bounded, start, end, source_range, out_dir, source_name, aspect_ratio, framing,
                          caption_style, editing_style, language, whisper_model, transcription, report):
    _, has_audio, _ = validate_local_video(Path(bounded), require_audio=False)
    report("download", 26, "The selected source range is ready")
    if has_audio and caption_style != "off":
        segments = transcribe_source(bounded, language, out_dir, caption_style, whisper_model, report,
                                     transcription=transcription)["segments"]
    else:
        segments = []
        report("transcribe", 54, "Transcription skipped")
    report("rank", 69, "Using the supplied timestamps")
    label = f"{source_timestamp(source_range[0])}–{source_timestamp(source_range[1])}"
    title = f"{Path(source_name).stem[:140]} | {label}" if source_name else f"Clip {label}"
    clips = crop_highlights_local(
        bounded, [{"title": title, "description": f"Source clip from {label}.",
                   "start_time": 0, "end_time": end - start, "score": None}],
        out_dir=out_dir, aspect_ratio=aspect_ratio, framing=framing, caption_style=caption_style,
        editing_style=editing_style, segments=segments, exact=True,
        on_activity=lambda index, total, message, portion, event: report(
            "render", 70 + int(19 * portion), message, event=event,
        ),
    )
    for clip in clips:
        clip.update(start_time=start, end_time=end)
    return clips


def generate_shorts(
    youtube_url: str,
    *,
    out_dir: str,
    uploaded_video: Optional[Path] = None,
    ranking: Optional[RankingConfig] = None,
    whisper_model: Optional[str] = None,
    transcription: Optional[Dict] = None,
    num_clips: int = 3,
    min_viral_score: Optional[int] = None,
    clip_length: tuple[int | None, int | None] = (None, None),
    aspect_ratio: str = "9:16",
    framing: str = "auto",
    caption_style: str = "classic",
    editing_style: str = "balanced",
    download_format: str = "720",
    language: Optional[str] = None,
    on_progress: Optional[Callable[..., None]] = None,
    source_range: tuple[int, int] | None = None,
    source_name: str = "",
) -> List[Dict]:
    """Render ranked highlights by count or score from a URL or an uploaded video."""
    gameplay = editing_style == "gameplay"
    if not gameplay and source_range is None:
        ranking = ranking or ranking_config()
        ranking.validate()
    if which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is required to render shorts. Install it and add it to your PATH.")

    report = progress_reporter(on_progress)
    if source_range is not None:
        bounded, start, end = prepare_bounded_source(youtube_url, uploaded_video, download_format, source_range, out_dir, report)
        return render_timestamp_clip(
            bounded, start, end, source_range, out_dir, source_name, aspect_ratio, framing, caption_style,
            editing_style, language, whisper_model, transcription, report,
        )
    source_path = prepare_source(youtube_url, uploaded_video, download_format, out_dir, report)
    if gameplay:
        duration, has_audio, _ = validate_local_video(Path(source_path), require_audio=False)
    elif uploaded_video is not None:
        validate_local_video(uploaded_video)
    report("download", 26, "Uploaded video is ready" if uploaded_video is not None else "YouTube source is ready")

    if gameplay and (not has_audio or caption_style == "off"):
        transcript = {"duration": duration, "segments": []}
        report("transcribe", 54, "Gameplay uses screen activity. Speech captions skipped.")
    else:
        transcript = transcribe_source(source_path, language, out_dir, caption_style, whisper_model, report,
                                       transcription=transcription)
    if not gameplay and not transcript["segments"]:
        raise RuntimeError(
            "Whisper produced no segments. The video may have no detectable speech."
        )

    if not gameplay:
        duration = transcript["duration"]
    if transcript["segments"]:
        report("transcribe", 54, f"Transcript ready · {len(transcript['segments'])} segments across {_clock(duration)}")
    minimum, maximum = clip_length
    if minimum and duration < minimum:
        report("rank", 55, f"The source is {_clock(duration)} long. Using the available duration.")
    if gameplay:
        report("rank", 56, "Finding sustained screen motion and audio changes")
        highlights_result = select_gameplay(source_path, duration, has_audio, transcript["segments"], clip_length, report)
    else:
        report("rank", 56, f"Analyzing the transcript with {PROVIDERS[ranking.provider][0]}")
        with ranking_client(ranking) as rank:
            def rank_at_checkpoint(prompt):
                report("rank", 56, "")
                return rank(prompt)

            highlights_result = get_highlights(
                transcript, num_clips=num_clips, llm_fn=rank_at_checkpoint, clip_length=clip_length,
                min_viral_score=min_viral_score, editing_style=editing_style,
                on_section=lambda index, total: report(
                    "rank", 58 + 10 * (index - 1) // total,
                    "Finding highlights" if total == 1 else f"Finding highlights · transcript section {index} of {total}",
                ),
            )
    all_highlights: List[Dict] = highlights_result.get("highlights", [])
    ranked = sorted(all_highlights, key=lambda highlight: highlight["score"], reverse=True)
    if min_viral_score is not None:
        top = [highlight for highlight in ranked if highlight["score"] >= min_viral_score]
        score_type = "activity" if gameplay else "virality"
        report("rank", 69, f"{len(top)} moments reached an {score_type} score of {min_viral_score} or higher"
               if gameplay else f"{len(top)} highlights scored {min_viral_score} or higher")
        if not top:
            return []
    elif ranked:
        top = ranked[:num_clips]
        report("rank", 69, f"Selected {len(top)} highlight{'s' if len(top) != 1 else ''}")
    else:
        if gameplay:
            raise NoMatchingHighlights("No sustained screen action was detected. Try footage with visible gameplay.")
        message = "No usable highlights fit this clip length. Try a wider range." if minimum or maximum else (
            "The ranking model found no usable highlights. Try another source or ranking model."
        )
        raise NoMatchingHighlights(message)

    print(f"[pipeline/local] cropping {len(top)} of {len(all_highlights)} candidates", flush=True)

    return crop_highlights_local(
        source_path, top, aspect_ratio=aspect_ratio, out_dir=out_dir,
        framing=framing, caption_style=caption_style, segments=transcript["segments"],
        editing_style=editing_style, minimum_duration=minimum,
        on_activity=lambda index, total, message, portion, event: report(
            "render", 70 + int(19 * (index - 1 + portion) / total),
            f"Clip {index} of {total} · {message}", event=event,
        ),
    )

