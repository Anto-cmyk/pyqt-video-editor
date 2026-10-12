
import json
import shutil
import subprocess
from pathlib import Path


FFMPEG_FOLDERS = [
    Path(r"C:\ffmpeg-9.0.2-essentials_build\bin"),
    Path(r"C:\ffmpeg\bin"),
]


def _find_executable(name):
    found = shutil.which(name)
    if found:
        return found

    for folder in FFMPEG_FOLDERS:
        candidate = folder / f"{name}.exe"
        if candidate.is_file():
            return str(candidate)

    raise FileNotFoundError(
        f"{name}.exe not found. Check your FFmpeg installation."
    )


def get_ffmpeg_path():
    return _find_executable("ffmpeg")


def get_ffprobe_path():
    return _find_executable("ffprobe")


def _run(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode:
        raise RuntimeError(result.stderr[-5000:] or "FFmpeg operation failed.")

    return result


def get_video_info(file_path):
    result = _run([
        get_ffprobe_path(),
        "-v", "error",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,width,height,codec_name",
        "-of", "json",
        str(Path(file_path).resolve()),
    ])

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )

    if video is None:
        raise ValueError("No readable video stream was found.")

    duration = float(data.get("format", {}).get("duration", 0) or 0)

    if duration <= 0:
        raise ValueError("Could not determine the video's duration.")

    return {
        "duration_seconds": duration,
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "video_codec": video.get("codec_name", "unknown"),
        "format": data.get("format", {}).get("format_name", "unknown"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }
