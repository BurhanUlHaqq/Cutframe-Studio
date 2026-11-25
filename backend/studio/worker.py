import logging
import time

from django.conf import settings
from django.utils import timezone
from filelock import FileLock

from shorts_generator import generate_shorts
from shorts_generator.local.llm import RankingConfigurationError
from shorts_generator.local.media import InvalidSourceVideo
from shorts_generator.pipeline import NoMatchingHighlights
from shorts_generator.reel import generate_highlight_reel

from .ai_settings import installation_ai, resolve_ai_options, saved_ai_options, transcription_options
from .exports import job_directory, make_export
from .models import ClipSelection, HighlightMethod, Job, StudioMode
from .uploads import uploaded_source

logger = logging.getLogger(__name__)


def failure_message(stage, exc):
    if isinstance(exc, (RankingConfigurationError, InvalidSourceVideo, NoMatchingHighlights)):
        return str(exc)
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in (401, 403):
        return "The ranking provider rejected its credentials. Check the API key in Settings and retry."
    if status == 404 and stage == "rank":
        return "The ranking provider could not find the model. Check the model ID and API base URL in Settings."
    if status == 429:
        return "The ranking provider returned a rate or quota limit. Check the account and retry."
    message = str(exc)
    if message.startswith("FFmpeg is required"):
        return "FFmpeg is missing from the render worker."
    if message.startswith(("Whisper produced no", "FFmpeg could not decode audio")):
        return "The source has no decodable speech. Try another video."
    return {
        "download": "YouTube did not provide a downloadable video. Check that the link is public and try again.",
        "transcribe": "The worker could not transcribe the audio. Check the Whisper setup and retry.",
        "rank": "The ranking provider did not return usable highlights. Check its configuration and retry.",
        "render": "The worker could not finish rendering the clips. Retry this render.",
        "export": "The worker could not package the clips. Check available disk space and retry.",
    }.get(stage, "The worker could not start this render. Check its configuration and retry.")


def process_next_job():
    job = Job.objects.filter(status=Job.Status.QUEUED).order_by("created_at").first()
    if job is None:
        return False

    record = Job.objects.filter(id=job.id)
    started_at = timezone.now()
    if not record.filter(status=Job.Status.QUEUED).update(
        status=Job.Status.RUNNING, stage="download", progress=1, activity="Preparing render", updated_at=started_at,
    ):
        return False

    stage = "download"
    progress = 1
    last_write = 0.0
    events = job.events[-39:]

    def report(next_stage, percent, message="", *, event=True, level="info"):
        nonlocal stage, progress, last_write
        now = time.monotonic()
        if next_stage == stage and not event and now - last_write < 0.4:
            return
        stage = next_stage
        progress = max(progress, percent)
        last_write = now
        updated = timezone.now()
        fields = {"stage": stage, "progress": progress, "updated_at": updated}
        if message:
            fields["activity"] = message
            if event:
                events.append({"time": updated.isoformat(), "stage": stage, "message": message, "level": level})
                del events[:-40]
                fields["events"] = events
        record.update(**fields)

    try:
        report(stage, progress, "Preparing render")
        work = job_directory(job.id) / "work"
        work.mkdir(parents=True, exist_ok=True)
        options = saved_ai_options(job.owner)
        installation = installation_ai()
        transcription = transcription_options(options, installation)
        min_viral_score = job.min_viral_score if job.selection_mode == ClipSelection.SCORE else None
        source_options = {
            "uploaded_video": uploaded_source(job.id) if job.source_name else None,
            "language": job.language or None, "caption_style": job.caption_style,
            "whisper_model": transcription.pop("whisper_model"), "transcription": transcription,
            "out_dir": str(work), "on_progress": report,
        }
        reel = job.studio_mode == StudioMode.HIGHLIGHTS
        if reel:
            ranking = None
            if job.highlight_method == HighlightMethod.TRANSCRIPT:
                try:
                    ranking = resolve_ai_options(options, installation)
                except RankingConfigurationError:
                    pass
            shorts = generate_highlight_reel(
                job.source_url, minutes=job.highlight_minutes, method=job.highlight_method,
                quality=job.quality, source_name=job.source_name, ranking=ranking, **source_options,
            )
        else:
            shorts = generate_shorts(
                job.source_url, num_clips=job.clip_count, aspect_ratio=job.aspect_ratio,
                min_viral_score=min_viral_score, clip_length=(job.min_clip_seconds, job.max_clip_seconds),
                download_format=job.quality, framing=job.framing, ranking=resolve_ai_options(options, installation),
                **source_options,
            )
        successful = sum(bool(clip.get("clip_url")) for clip in shorts)
        for index, clip in enumerate(shorts, 1):
            if not clip.get("clip_url"):
                report("render", 89, f"Clip {index} could not be rendered", level="warning")
        if successful:
            export_message = "Packaging the highlight reel" if reel else f"Packaging the ZIP with {successful} clip{'s' if successful != 1 else ''}"
            report("export", 92, export_message)
            clips = make_export(job, shorts)
            message = "ZIP ready to download"
        elif shorts or min_viral_score is None:
            raise RuntimeError("No clips rendered successfully.")
        else:
            clips = []
            message = f"No highlights scored {min_viral_score} or higher"
        now = timezone.now()
        events.append({"time": now.isoformat(), "stage": "complete", "message": message, "level": "info"})
        record.update(
            status=Job.Status.COMPLETE, stage="complete", progress=100,
            clips=clips, error="", activity=message, events=events[-40:], updated_at=now,
        )
    except Exception as exc:
        logger.error("Render %s failed in %s (%s)", job.id, stage, type(exc).__name__)
        now = timezone.now()
        message = failure_message(stage, exc)
        events.append({"time": now.isoformat(), "stage": stage, "message": message, "level": "error"})
        record.update(
            status=Job.Status.FAILED, error=message, activity=message, events=events[-40:], updated_at=now,
        )
    return True


def run_queue(once=False):
    """Keep one active worker per data volume and resume interrupted renders."""
    with FileLock(settings.STUDIO_DATA_DIR / "worker.lock", timeout=0):
        Job.objects.filter(status=Job.Status.RUNNING).update(
            status=Job.Status.QUEUED, stage="queue", progress=0,
            activity="Waiting to resume after worker restart", updated_at=timezone.now(),
        )
        while True:
            processed = process_next_job()
            if once:
                return
            if not processed:
                time.sleep(1)
