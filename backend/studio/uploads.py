"""Bounded multipart intake and storage for uploaded source media."""

from pathlib import Path

from django.conf import settings
from django.core.files.move import file_move_safe
from django.http import JsonResponse

from .exports import job_directory


VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts")


class UploadLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path_info == "/api/jobs/upload/":
            value = request.META.get("CONTENT_LENGTH", "")
            if not value or not value.isascii() or not value.isdecimal() or len(value) > 20:
                return JsonResponse({"error": "Send the upload with a known file size."}, status=411)
            if int(value) > settings.STUDIO_MAX_UPLOAD_BYTES + 64 * 1024:
                return JsonResponse({"error": "This video exceeds the workspace upload limit."}, status=413)
        return self.get_response(request)


def uploaded_source(job_id):
    return job_directory(job_id) / "input" / "source"


def store_upload(job_id, video):
    source = uploaded_source(job_id)
    source.parent.mkdir(parents=True, exist_ok=False)
    try:
        if hasattr(video, "temporary_file_path"):
            file_move_safe(video.temporary_file_path(), source)
        else:
            with source.open("xb") as handle:
                for chunk in video.chunks():
                    handle.write(chunk)
    except Exception:
        source.unlink(missing_ok=True)
        source.parent.rmdir()
        raise
    return source
