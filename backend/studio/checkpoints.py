"""Recover completed render assets and automatic covers after an export failure."""

import hashlib
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory


logger = logging.getLogger(__name__)
RENDER_MANIFEST = ".render-artifacts.json"


def _store(path, content):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".checkpoint-", dir=path.parent) as staging:
            pending = Path(staging) / path.name
            pending.write_bytes(content)
            pending.replace(path)
    except OSError as exc:
        logger.warning("Could not save render checkpoint (%s)", type(exc).__name__)


def save_render_checkpoint(work, clips):
    if clips and all(clip.get("clip_url") for clip in clips):
        try:
            content = json.dumps({"version": 1, "clips": clips}, default=os.fspath).encode()
        except (TypeError, ValueError):
            logger.warning("Could not serialize render checkpoint")
        else:
            _store(work / RENDER_MANIFEST, content)


def load_render_checkpoint(work):
    root = work.resolve()
    try:
        payload = json.loads((root / RENDER_MANIFEST).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        clips = payload.get("clips")
        if not isinstance(clips, list) or not clips:
            return None
        for clip in clips:
            required = {"clip_url", "score", "start_time", "end_time"}
            if not isinstance(clip, dict) or not required <= clip.keys() or not clip["clip_url"]:
                return None
            if ("chapters" in clip and "chapter_text" not in clip
                    or clip.get("pauses_trimmed") and "duration_seconds" not in clip):
                return None
            paths = [clip[field] for field in ("clip_url", "thumbnail_path", "caption_path") if clip.get(field)]
            if options := clip.get("thumbnail_options"):
                if "thumbnail_design" not in clip:
                    return None
                for option in options:
                    if not isinstance(option, dict) or not {"path", "time", "placement"} <= option.keys():
                        return None
                    paths.append(option["path"])
            for value in paths:
                artifact = Path(value).resolve()
                if not artifact.is_relative_to(root) or not artifact.is_file() or not artifact.stat().st_size:
                    return None
        return clips
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return None


def cover_checkpoint_path(work, provider, clip, ratio, reference):
    source_digest = ""
    if provider.provider == "openai" and reference.is_file():
        try:
            with reference.open("rb") as source:
                source_digest = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError:
            return None
    context = {"version": 1, "provider": provider.provider, "endpoint": provider.base_url, "model": provider.model,
               "quality": provider.quality, "ratio": ratio, "reference": source_digest,
               "clip": {field: clip.get(field) for field in ("title", "description", "thumbnail_design")}}
    digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    return work / ".cover-checkpoints" / f"{digest}.jpg"


def load_cover_checkpoint(path):
    try:
        content = path.read_bytes()
        if content.startswith(b"\xff\xd8") and content.endswith(b"\xff\xd9"):
            return content
    except OSError:
        pass
    return None


def save_cover_checkpoint(path, content):
    _store(path, content)
