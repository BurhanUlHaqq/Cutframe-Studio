import logging
import time

from django.conf import settings
from django.utils import timezone
from filelock import FileLock

from shorts_generator import generate_shorts
from shorts_generator.control import RenderCancelled
from shorts_generator.edit_styles import STYLES
from shorts_generator.local.llm import RankingConfigurationError
from shorts_generator.local.media import InvalidSourceVideo
from shorts_generator.pipeline import NoMatchingHighlights
from shorts_generator.reel import generate_highlight_reel

from .ai_settings import installation_ai, resolve_ai_options, saved_ai_options, transcription_options
from .checkpoints import (
    cover_checkpoint_path, load_cover_checkpoint, load_render_checkpoint, save_cover_checkpoint, save_render_checkpoint,
)
from .descriptions import BATCH_SIZE, DescriptionError, generate_descriptions, saved_description_options, writer_config
from .exports import job_directory, make_export, set_ai_thumbnail, thumbnail_reference
from .models import ClipSelection, HighlightMethod, Job, StudioMode
from .source_reuse import stage_alternate_source
from .thumbnail_ai import ThumbnailGenerationError, image_provider, make_ai_cover, saved_thumbnail_ai
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
        "covers": "The worker could not save the thumbnail. Check available disk space and retry.",
    }.get(stage, "The worker could not start this render. Check its configuration and retry.")


def enrich_descriptions(job, shorts, ranking_options, installation, report):
    options = saved_description_options(job.owner)
    if not options["automatic"]:
        return
    try:
        config = writer_config(options, ranking_options, installation)
        config.validate()
    except RankingConfigurationError as exc:
        report("export", 92, f"AI descriptions unavailable. {exc}", level="warning")
        return
    ready = [clip for clip in shorts if clip.get("clip_url")]
    for offset in range(0, len(ready), BATCH_SIZE):
        batch = ready[offset:offset + BATCH_SIZE]
        report("export", 92, f"Writing AI descriptions for videos {offset + 1}–{offset + len(batch)}")
        try:
            descriptions = generate_descriptions(config, batch, language=job.language)
        except DescriptionError as exc:
            report("export", 92, f"Kept the original descriptions. {exc}", level="warning")
            return
        for index, description in descriptions.items():
            batch[index].update(description=description, hashtags=[])
        if len(descriptions) < len(batch):
            report("export", 92, "Some videos kept their original descriptions.", level="warning")


def finish_cancelled(record):
    stopping = record.filter(status=Job.Status.CANCELLING)
    events = stopping.values_list("events", flat=True).first()
    if events is None:
        return
    now = timezone.now()
    message = "Render cancelled. You can run it again or remove it from the Library."
    event = {"time": now.isoformat(), "stage": "cancelled", "message": message, "level": "info"}
    stopping.update(status=Job.Status.CANCELLED, stage="cancelled", activity=message,
                    error="", events=[*events[-39:], event], updated_at=now)


def render_source(job, work, options, installation, minimum_score, report):
    stage_alternate_source(job, work, report)
    transcription = transcription_options(options, installation)
    source_range = (job.source_start_ms, job.source_end_ms) if job.source_end_ms is not None else None
    source_options = {
        "uploaded_video": uploaded_source(job.id) if job.source_name else None,
        "language": job.language or None, "caption_style": job.caption_style,
        "whisper_model": transcription.pop("whisper_model"), "transcription": transcription,
        "out_dir": str(work), "on_progress": report, "source_range": source_range,
    }
    if job.studio_mode == StudioMode.HIGHLIGHTS:
        ranking = None
        if job.highlight_method == HighlightMethod.TRANSCRIPT:
            try:
                ranking = resolve_ai_options(options, installation)
            except RankingConfigurationError:
                pass
        return generate_highlight_reel(
            job.source_url, minutes=job.highlight_minutes, method=job.highlight_method,
            quality=job.quality, source_name=job.source_name, ranking=ranking, **source_options,
        )
    report("download", 1, f"{STYLES[job.editing_style].label} editing style")
    return generate_shorts(
        job.source_url, num_clips=job.clip_count, aspect_ratio=job.aspect_ratio,
        min_viral_score=minimum_score, clip_length=(job.min_clip_seconds, job.max_clip_seconds),
        download_format=job.quality, framing=job.framing, editing_style=job.editing_style,
        ranking=None if source_range or job.editing_style == "gameplay" else resolve_ai_options(options, installation),
        source_name=job.source_name, **source_options,
    )


def process_next_job():
    job = Job.objects.filter(status=Job.Status.QUEUED).order_by("created_at").first()
    if job is None:
        return False

    record = Job.objects.filter(id=job.id)
    running = record.filter(status=Job.Status.RUNNING)
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
        if not running.update(**fields):
            raise RenderCancelled

    try:
        report(stage, progress, "Preparing render")
        work = job_directory(job.id) / "work"
        work.mkdir(parents=True, exist_ok=True)
        options = saved_ai_options(job.owner)
        installation = installation_ai()
        min_viral_score = job.min_viral_score if job.selection_mode == ClipSelection.SCORE else None
        shorts = load_render_checkpoint(work)
        if shorts is not None:
            videos = "video" if len(shorts) == 1 else "videos"
            report("export", 90, f"Resuming export with {len(shorts)} completed {videos}")
        else:
            shorts = render_source(job, work, options, installation, min_viral_score, report)
            save_render_checkpoint(work, shorts)
        successful = sum(bool(clip.get("clip_url")) for clip in shorts)
        for index, clip in enumerate(shorts, 1):
            if not clip.get("clip_url"):
                report("render", 89, f"Clip {index} could not be rendered", level="warning")
        if successful:
            enrich_descriptions(job, shorts, options, installation, report)
            provider = None
            covers = saved_thumbnail_ai(job.owner)
            cover_count = min(successful, covers.get("automatic_limit") or successful)
            if covers.get("provider") and covers.get("automatic"):
                try:
                    provider = image_provider(job.owner, covers)
                except ThumbnailGenerationError as exc:
                    report("export", 92, f"AI covers unavailable. {exc}", level="warning")
            if provider and cover_count < successful:
                report("export", 92, f"Automatic AI cover limit set to {cover_count} of {successful} videos.")

            def generate_cover(files, clip, index):
                nonlocal provider
                if provider is None or index > cover_count:
                    return
                if running.filter(skip_ai_covers=True).exists():
                    report("export", progress, "Remaining AI covers skipped. Using the frame covers for those videos.")
                    provider = None
                    return
                reference = thumbnail_reference(files, clip)
                cached_path = cover_checkpoint_path(work, provider, clip, job.aspect_ratio, reference)
                cached = load_cover_checkpoint(cached_path) if cached_path else None
                verb = "Reusing" if cached else "Generating"
                report("covers", 92 + round(5 * (index - 1) / cover_count), f"{verb} AI cover {index} of {cover_count}")
                try:
                    content = cached or make_ai_cover(provider, clip, job.aspect_ratio, work, reference=reference)
                    set_ai_thumbnail(files, clip, content, provider.model)
                    if not cached and cached_path:
                        save_cover_checkpoint(cached_path, content)
                except ThumbnailGenerationError as exc:
                    if exc.stop_batch:
                        provider = None
                        report("export", progress, f"Remaining videos will use frame covers. {exc}", level="warning")
                    else:
                        report("export", progress, f"Clip {index} kept its frame cover. {exc}", level="warning")

            def export_progress(step, index, total, event):
                if step == "prepare":
                    report("export", 92 + 4 * (index - 1) // total, f"Preparing video {index} of {total} for export", event=event)
                else:
                    report("export", 97 + 2 * (index - 1) // total, f"Writing ZIP · video {index} of {total}", event=event)

            report("export", 92, "Preparing completed videos for export")
            clips = make_export(job, shorts, generate_cover=generate_cover if provider else None, on_activity=export_progress)
            message = "ZIP ready to download"
        elif shorts or min_viral_score is None:
            raise RuntimeError("No clips rendered successfully.")
        else:
            clips = []
            message = (f"No moments reached an activity score of {min_viral_score}" if job.editing_style == "gameplay"
                       else f"No highlights scored {min_viral_score} or higher")
        now = timezone.now()
        events.append({"time": now.isoformat(), "stage": "complete", "message": message, "level": "info"})
        if not running.update(
            status=Job.Status.COMPLETE, stage="complete", progress=100,
            clips=clips, error="", activity=message, events=events[-40:], updated_at=now,
        ):
            finish_cancelled(record)
    except RenderCancelled:
        finish_cancelled(record)
    except Exception as exc:
        now = timezone.now()
        message = failure_message(stage, exc)
        events.append({"time": now.isoformat(), "stage": stage, "message": message, "level": "error"})
        if running.update(
            status=Job.Status.FAILED, error=message, activity=message, events=events[-40:], updated_at=now,
        ):
            logger.error("Render %s failed in %s (%s)", job.id, stage, type(exc).__name__)
        else:
            finish_cancelled(record)
    return True


def run_queue(once=False):
    """Keep one active worker per data volume and resume interrupted renders."""
    with FileLock(settings.STUDIO_DATA_DIR / "worker.lock", timeout=0):
        for job_id in Job.objects.filter(status=Job.Status.CANCELLING).values_list("id", flat=True).iterator():
            finish_cancelled(Job.objects.filter(pk=job_id))
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
