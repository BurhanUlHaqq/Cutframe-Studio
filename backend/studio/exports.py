import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, ZipFile

from django.conf import settings
from filelock import FileLock

from shorts_generator.local.thumbnails import write_thumbnail


def job_directory(job_id):
    return settings.STUDIO_DATA_DIR / "jobs" / str(job_id)


def publication_filename(title):
    """Preserve case and spaces while making the title safe on common filesystems."""
    name = " ".join(re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title).split()).rstrip(".")
    name = name.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .") or "Clip"
    if re.fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]", name.split(".", 1)[0].rstrip(), re.IGNORECASE):
        name = "_" + name
    return name


def clip_text(clip):
    return f"{clip['title']}\n\n{clip['description']}\n".encode("utf-8")


def _write_titled_video(source, destination, title):
    with TemporaryDirectory(prefix=".title-", dir=destination.parent) as temporary:
        pending = Path(temporary) / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(source), "-map", "0", "-c", "copy",
             "-map_metadata", "-1", "-metadata", f"title={title}", "-movflags", "+faststart", str(pending)],
            check=True, capture_output=True,
        )
        pending.replace(destination)


def open_published_video(directory, clip):
    source = directory / "files" / f"{clip['name']}.mp4"
    title = clip["title"]
    if title == clip.get("embedded_title"):
        return source.open("rb")

    # Older renders and title edits get a lossless remux for downloads.
    cache = directory / "published"
    cache.mkdir(exist_ok=True)
    revision = hashlib.sha256(title.encode("utf-8")).hexdigest()[:20]
    final = cache / f"{clip['name']}-{revision}.mp4"
    with FileLock(cache / f"{clip['name']}.lock"):
        if not final.is_file():
            if not source.is_file():
                raise FileNotFoundError(source)
            _write_titled_video(source, final, title)
        handle = final.open("rb")
        for old in cache.glob(f"{clip['name']}-*.mp4"):
            if old != final:
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    pass
        return handle


def open_archive(job_id, clips):
    """Open the ZIP for this metadata revision, rebuilding it only if needed."""
    directory = job_directory(job_id)
    fingerprint = hashlib.sha256(json.dumps({"version": 2, "clips": clips}, sort_keys=True).encode()).hexdigest()[:20]
    final = directory / f"clips-{fingerprint}.zip"
    with FileLock(directory / "export.lock"):
        if not final.is_file():
            pending = final.with_suffix(".part")
            with ZipFile(pending, "w", compression=ZIP_STORED) as archive:
                for index, clip in enumerate(clips, 1):
                    name = f"{index:02d}/{publication_filename(clip['title'])}"
                    with open_published_video(directory, clip) as video, archive.open(f"{name}.mp4", "w", force_zip64=True) as member:
                        shutil.copyfileobj(video, member, length=1024 * 1024)
                    archive.write(directory / "files" / f"{clip['name']}.jpg", f"{name}.jpg")
                    if clip.get("has_captions"):
                        subtitles = directory / "files" / f"{clip['name']}.srt"
                        archive.write(subtitles, f"{name}.srt")
                    archive.writestr(f"{name}.txt", clip_text(clip))
            pending.replace(final)

        handle = final.open("rb")
        for old in directory.glob("clips*.zip"):
            if old != final:
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    # A Windows download can keep the previous revision open.
                    pass
        return handle


def make_export(job, shorts):
    """Create a ZIP and a matching set of browser-previewable files."""
    directory = job_directory(job.id)
    files = directory / "files"
    files.mkdir(parents=True, exist_ok=True)
    clips = []
    work = (directory / "work").resolve()
    for short in shorts:
        if not short.get("clip_url"):
            continue

        index = len(clips) + 1
        title = " ".join(str(short.get("title") or "").split())[:180] or f"Clip {index}"
        description = (
            short.get("description") or short.get("hook_sentence")
            or short.get("virality_reason") or title
        ).strip()[:1200]
        name = f"clip-{index:02d}"
        video = files / f"{name}.mp4"
        thumbnail = files / f"{name}.jpg"

        source = Path(short["clip_url"]).resolve()
        if not source.is_relative_to(work):
            raise ValueError("Clip was rendered outside its job directory.")
        _write_titled_video(source, video, title)
        if short.get("thumbnail_path"):
            cover = Path(short["thumbnail_path"]).resolve()
            if not cover.is_relative_to(work):
                raise ValueError("Thumbnail was rendered outside its job directory.")
            shutil.copyfile(cover, thumbnail)
        else:
            write_thumbnail(video, thumbnail)
        metadata = {
            "name": name,
            "title": title,
            "embedded_title": title,
            "description": description,
            "score": short["score"],
            "start_time": short["start_time"],
            "end_time": short["end_time"],
        }
        if short.get("caption_path"):
            subtitles = Path(short["caption_path"]).resolve()
            if not subtitles.is_relative_to(work):
                raise ValueError("Caption track was rendered outside its job directory.")
            shutil.copyfile(subtitles, files / f"{name}.srt")
            metadata["has_captions"] = True
        clips.append(metadata)

    if not clips:
        raise RuntimeError("No clips rendered successfully.")
    open_archive(job.id, clips).close()
    return clips
