"""Stage the original media for an alternate render."""

from filelock import Timeout

from shorts_generator.local.downloader import cached_download
from shorts_generator.local.media import InvalidSourceVideo
from shorts_generator.local.transcriber import transcript_cache_path, transfer_transcript_cache

from .exports import job_directory, render_lock
from .models import Job
from .uploads import stored_source, uploaded_source


def sources_in_use(job_ids):
    """Identify uploads whose alternate renders still need their original files."""
    alternates = (Job.objects.filter(source_render_id__in=job_ids).exclude(source_name="")
                  .values_list("source_render_id", "id"))
    return {source_id for source_id, job_id in alternates.iterator() if not stored_source(uploaded_source(job_id))}


def copy_source(source, destination, report):
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(destination.suffix + ".copying")
    received = 0
    try:
        pending.unlink(missing_ok=True)
        with source.open("rb") as original, pending.open("xb") as output:
            while chunk := original.read(8 * 1024 * 1024):
                output.write(chunk)
                received += len(chunk)
                report("download", 4, f"Preparing source video · {received / 1024**2:.1f} MB copied", event=False)
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)


def stage_alternate_source(job, work, report):
    if not job.source_render_id:
        return
    upload = uploaded_source(job.id) if job.source_name else None
    prepared = (upload if stored_source(upload) else None) if upload else cached_download(work, job.video_id)
    if prepared and transcript_cache_path(prepared, work).is_file():
        return
    needs_upload = upload and not prepared

    try:
        with render_lock(job.source_render_id).acquire(timeout=10 if needs_upload else 0):
            original = Job.objects.filter(pk=job.source_render_id, owner=job.owner).first()
            source = None
            if original:
                original_work = job_directory(original.id) / "work"
                if upload:
                    source = uploaded_source(original.id)
                elif original.status == Job.Status.COMPLETE and original.quality == job.quality:
                    source = cached_download(original_work, original.video_id)
            if not source or not stored_source(source):
                if needs_upload:
                    raise InvalidSourceVideo("The original upload is unavailable. Start a new render and choose the video again.")
                return
            if prepared:
                transfer_transcript_cache(
                    source, original_work, prepared, work,
                    on_verify=lambda checked: report(
                        "download", 4, f"Verifying saved source video · {checked / 1024**2:.1f} MB checked", event=False,
                    ),
                )
                return
            destination = upload or work / source.name
            report("download", 3, "Reusing the original uploaded video" if upload else "Preparing the saved YouTube download")
            copy_source(source, destination, report)
            transfer_transcript_cache(source, original_work, destination, work)
    except (OSError, Timeout) as failure:
        if needs_upload:
            message = ("The source render is busy. Try again when its current operation finishes." if isinstance(failure, Timeout)
                       else "Cutframe could not copy the original upload. Check available disk space and retry.")
            raise InvalidSourceVideo(message) from failure
