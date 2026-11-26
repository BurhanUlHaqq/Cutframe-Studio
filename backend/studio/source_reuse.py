"""Stage the original media for an alternate render."""

from filelock import Timeout

from shorts_generator.local.downloader import cached_download
from shorts_generator.local.media import InvalidSourceVideo

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
    if upload and stored_source(upload):
        return
    if not upload and cached_download(work, job.video_id):
        return

    try:
        with render_lock(job.source_render_id).acquire(timeout=10 if upload else 0):
            original = Job.objects.filter(pk=job.source_render_id, owner=job.owner).first()
            if upload:
                source = uploaded_source(original.id) if original else None
                if not source or not stored_source(source):
                    raise InvalidSourceVideo("The original upload is unavailable. Start a new render and choose the video again.")
                report("download", 3, "Reusing the original uploaded video")
                copy_source(source, upload, report)
            elif original and original.status == Job.Status.COMPLETE and original.quality == job.quality:
                cached = cached_download(job_directory(original.id) / "work", original.video_id)
                if cached and stored_source(cached):
                    report("download", 3, "Preparing the saved YouTube download")
                    copy_source(cached, work / cached.name, report)
    except (OSError, Timeout) as failure:
        if upload:
            message = ("The source render is busy. Try again when its current operation finishes." if isinstance(failure, Timeout)
                       else "Cutframe could not copy the original upload. Check available disk space and retry.")
            raise InvalidSourceVideo(message) from failure
