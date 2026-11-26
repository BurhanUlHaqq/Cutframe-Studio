"""Local transcription via faster-whisper.

Reads a local media file and caches the transcript with Whisper word timing.
"""
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np

from ..config import DEFAULT_VAD_PARAMETERS, DEFAULT_WHISPER_MODEL


def _media_stamp(source):
    stat = source.stat()
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def transcript_cache_path(source, out_dir):
    return Path(out_dir) / f"{source.stem}.transcript.json"


def _read_word_transcript(cache_path):
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (isinstance(cached["source"], dict) and isinstance(cached["segments"], list) and cached["segments"]
                and math.isfinite(cached["duration"]) and cached["duration"] > 0):
            return cached
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _write_transcript(cache_path, transcript):
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pending = cache_path.with_suffix(".part")
    try:
        pending.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        pending.replace(cache_path)
    finally:
        pending.unlink(missing_ok=True)


def _same_media(source, copied_source, on_bytes):
    checked = 0
    with source.open("rb") as original, copied_source.open("rb") as copy:
        while chunk := original.read(8 * 1024 * 1024):
            if copy.read(len(chunk)) != chunk:
                return False
            checked += len(chunk)
            on_bytes(checked)
    return True


def transfer_transcript_cache(source, original_work, copied_source, work, *, on_verify=None):
    """Carry word timings to an identical source. Verify copies left by earlier runs."""
    original_cache = transcript_cache_path(source, original_work)
    target_cache = transcript_cache_path(copied_source, work)
    if original_cache.is_symlink() or target_cache.exists():
        return
    cached = _read_word_transcript(original_cache)
    if not cached or not all(isinstance(segment, dict) and isinstance(segment.get("words"), list)
                             for segment in cached["segments"]):
        return
    try:
        original_stamp = _media_stamp(source)
        target_stamp = _media_stamp(copied_source)
        identity = cached["source"]
        if original_stamp["size"] != target_stamp["size"] or any(
            identity.get(key) != value for key, value in original_stamp.items()
        ):
            return
        if on_verify is not None and not _same_media(source, copied_source, on_verify):
            return
        _write_transcript(target_cache, {**cached, "source": {**identity, **target_stamp}})
    except OSError:
        # A missing cache or a disk write failure still allows normal transcription.
        pass


def _parse_srt_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + (millis / 1000.0)


def _load_srt_cache(cache_path: Path) -> Dict:
    content = cache_path.read_text(encoding="utf-8-sig").strip()
    if not content:
        return {"duration": 0.0, "segments": []}

    segments = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if "-->" not in lines[0] and len(lines) > 1 and "-->" in lines[1]:
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_raw, end_raw = [part.strip() for part in lines[0].split("-->", 1)]
        text = "\n".join(lines[1:]).strip()
        segments.append(
            {
                "start": _parse_srt_timestamp(start_raw),
                "end": _parse_srt_timestamp(end_raw),
                "text": text,
            }
        )

    duration = segments[-1]["end"] if segments else 0.0
    return {"duration": duration, "segments": segments}


def _resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            # Test that CUDA actually works (catches missing cuBLAS/cuDNN libs)
            torch.zeros(1, device="cuda")
            return "cuda"
    except (ImportError, OSError, RuntimeError):
        pass
    return "cpu"


def _decode_audio(media_path: str, sample_rate: int) -> np.ndarray:
    """Decode the first audio stream to the mono waveform Whisper accepts."""
    command = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file", "-i", media_path,
        "-map", "0:a:0", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"FFmpeg could not decode audio from {media_path}: {detail}") from exc
    return np.frombuffer(result.stdout, dtype="<f4")


def transcribe_local(
    media_path: str, *, out_dir: str, language: Optional[str] = None, allow_legacy: bool = False,
    whisper_model: Optional[str] = None,
    whisper_device: str = "auto", vad_filter: bool = False, vad_parameters: Optional[Dict] = None,
    on_progress: Optional[Callable[[float, float], None]] = None,
    on_cache: Optional[Callable[[], None]] = None,
) -> Dict:
    """Run Whisper or reuse a transcript matching this source, model, and language."""
    source = Path(media_path)
    model_name = whisper_model or DEFAULT_WHISPER_MODEL
    vad_options = DEFAULT_VAD_PARAMETERS | (vad_parameters or {}) if vad_filter else {}
    stamp = _media_stamp(source)
    cache_path = transcript_cache_path(source, out_dir)
    identity = {**stamp, "model": model_name, "language": language}
    if vad_filter:
        identity["vad"] = vad_options
    cached = _read_word_transcript(cache_path)
    if cached and cached["source"] == identity:
        print(f"[transcribe/local] reusing word timings: {cache_path}", flush=True)
        if on_cache is not None:
            on_cache()
        return cached

    legacy = Path(out_dir) / f"{source.stem}.srt"
    if allow_legacy and not vad_filter and legacy.is_file() and legacy.stat().st_mtime_ns >= stamp["mtime_ns"]:
        try:
            cached = _load_srt_cache(legacy)
            if cached["segments"] and cached["duration"] > 0:
                if on_cache is not None:
                    on_cache()
                return cached
        except (OSError, ValueError):
            pass

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "faster-whisper is required to transcribe videos. Install it with:\n"
            "    pip install -r requirements.txt"
        ) from e

    device = _resolve_device(whisper_device)
    compute_type = "float16" if device == "cuda" else "int8"
    print(f"[transcribe/local] faster-whisper model={model_name} device={device}", flush=True)

    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    transcribe_kwargs = {
        "audio": _decode_audio(media_path, model.feature_extractor.sampling_rate),
        "language": language,
        "beam_size": 5,
        "condition_on_previous_text": False,
        "word_timestamps": True,
    }
    if vad_filter:
        transcribe_kwargs["vad_filter"] = True
        transcribe_kwargs["vad_parameters"] = vad_options
    else:
        transcribe_kwargs["vad_filter"] = False

    segments_iter, info = model.transcribe(**transcribe_kwargs)

    duration = float(getattr(info, "duration", 0.0))
    segments = []
    for s in segments_iter:
        segments.append({
            "start": float(s.start),
            "end": float(s.end),
            "text": (s.text or "").strip(),
            "words": [
                {"start": float(word.start), "end": float(word.end), "text": word.word}
                for word in (s.words or [])
                if word.start is not None and word.end is not None
            ],
        })
        if on_progress is not None and duration:
            on_progress(min(float(s.end), duration), duration)

    duration = duration or (segments[-1]["end"] if segments else 0.0)
    print(f"[transcribe/local] {len(segments)} segments, {duration:.0f}s of audio", flush=True)
    transcript = {"source": identity, "duration": duration, "segments": segments}
    _write_transcript(cache_path, transcript)
    print(f"[transcribe/local] wrote cache: {cache_path}", flush=True)
    return transcript
