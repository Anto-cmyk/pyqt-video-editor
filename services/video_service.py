import re
import subprocess
import tempfile
from pathlib import Path

from services.ffmpeg_service import get_ffmpeg_path, get_video_info

RESOLUTIONS = {
    "720 × 1280 (Portrait)": (720, 1280),
    "1080 × 1920 (Portrait HD)": (1080, 1920),
    "1080 × 1080 (Square)": (1080, 1080),
    "1280 × 720 (Landscape)": (1280, 720),
    "1920 × 1080 (Landscape HD)": (1920, 1080),
}

QUALITY = {
    "Fast (larger file)": ("ultrafast", 23),
    "Balanced": ("veryfast", 21),
    "High quality (slower)": ("medium", 18),
}

MODES = ("Cover", "Blur", "Pixelate", "Inpaint")

# Marks placed within this many seconds of each other form one group.
MARK_JOIN_SECONDS = 0.75

_watermark = None
_export = {
    "width": 720,
    "height": 1280,
    "fps": 30,
    "preset": "ultrafast",
    "crf": 23,
    "mute": False,
}
_size_cache = {}


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def set_export_settings(settings):
    """Update resolution / quality / fps / mute."""
    if not settings:
        return
    for key in _export:
        if key in settings:
            _export[key] = settings[key]


def _num(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def set_watermark_settings(settings):
    """Set or clear watermark removal settings.

    settings = {
        "mode": "Cover" | "Blur" | "Pixelate" | "Inpaint",
        "trim_end": seconds to cut from the end of each source file,
        "regions": [   # percent of the export canvas
            {"x": %, "y": %, "width": %, "height": %,
             "start": 0..1, "end": 0..1}   # fraction of the source video
        ],
        "marks": [     # clicked on the preview: percent of the video frame
            {"x": %, "y": %, "w": %, "h": %, "t": timeline seconds}
        ],
    }
    """
    global _watermark

    if not settings or settings.get("mode", "None") == "None":
        _watermark = None
        return

    mode = str(settings.get("mode", "Cover"))
    if mode not in MODES:
        raise ValueError("Choose Cover, Blur, Pixelate, Inpaint, or None.")

    regions = []
    for r in settings.get("regions", []) or []:
        w = max(0.5, min(100.0, _num(r.get("width"), 25)))
        h = max(0.5, min(100.0, _num(r.get("height"), 10)))
        x = max(0.0, min(100.0 - w, _num(r.get("x"), 75)))
        y = max(0.0, min(100.0 - h, _num(r.get("y"), 2)))
        start = max(0.0, min(1.0, _num(r.get("start"), 0.0)))
        end = max(0.0, min(1.0, _num(r.get("end"), 1.0)))
        if end <= start:
            continue
        regions.append(
            {"x": x, "y": y, "width": w, "height": h,
             "start": start, "end": end}
        )

    marks = []
    for m in settings.get("marks", []) or []:
        w = max(0.5, min(100.0, _num(m.get("w"), 10)))
        h = max(0.5, min(100.0, _num(m.get("h"), 5)))
        x = max(0.0, min(100.0 - w, _num(m.get("x"), 0)))
        y = max(0.0, min(100.0 - h, _num(m.get("y"), 0)))
        t = max(0.0, _num(m.get("t"), 0))
        marks.append({"x": x, "y": y, "w": w, "h": h, "t": t})

    _watermark = {
        "mode": mode,
        "trim_end": max(0.0, _num(settings.get("trim_end"), 0)),
        "regions": regions,
        "marks": marks,
    }


def mark_windows(marks, join=MARK_JOIN_SECONDS):
    """Group marks by the moment they were placed.

    Each group is active from its own time until the next group's time.
    The first group starts at 0 and the last runs to the end, so a single
    mark covers the whole video.

    Returns [{"t0": float|None, "t1": float|None, "items": [(index, mark)]}]
    """
    groups = []

    for index, mark in sorted(enumerate(marks), key=lambda p: p[1]["t"]):
        t = float(mark["t"])
        if groups and t - groups[-1]["t"] < join:
            groups[-1]["items"].append((index, mark))
        else:
            groups.append({"t": t, "items": [(index, mark)]})

    for i, group in enumerate(groups):
        group["t0"] = None if i == 0 else group["t"]
        group["t1"] = groups[i + 1]["t"] if i + 1 < len(groups) else None

    return groups


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _source_start(segment):
    """The player uses `source_start`; older code used `start`."""
    for name in ("source_start", "start"):
        value = getattr(segment, name, None)
        if value is not None:
            return float(value)
    return 0.0


def _even(value):
    return max(2, int(value) // 2 * 2)


def _video_size(path):
    """Displayed (width, height) of a video, rotation applied, or None."""
    key = str(path)
    if key in _size_cache:
        return _size_cache[key]

    size = None
    try:
        result = subprocess.run(
            [get_ffmpeg_path(), "-hide_banner", "-i", key],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        text = result.stderr or ""
        match = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})\b", text)
        if match:
            w, h = int(match.group(1)), int(match.group(2))
            rot = re.search(r"rotation of (-?\d+(?:\.\d+)?) degrees", text)
            if rot and int(round(abs(float(rot.group(1))))) % 180 == 90:
                w, h = h, w
            size = (w, h)
    except Exception:
        size = None

    _size_cache[key] = size
    return size


def _mark_regions(path, marks, timeline_offset):
    """Turn clicked marks into canvas regions with time windows.

    Marks are percentages of the source frame. The export scales the source
    to fit the canvas and pads the rest, so convert through that layout.
    """
    cw, ch = _export["width"], _export["height"]
    sw, sh = _video_size(path) or (cw, ch)

    scale = min(cw / sw, ch / sh)
    iw, ih = sw * scale, sh * scale
    ox, oy = (cw - iw) / 2, (ch - ih) / 2

    regions = []

    for group in mark_windows(marks):
        a = None if group["t0"] is None else group["t0"] - timeline_offset
        b = None if group["t1"] is None else group["t1"] - timeline_offset

        for _, m in group["items"]:
            regions.append({
                "x": (ox + m["x"] / 100 * iw) / cw * 100,
                "y": (oy + m["y"] / 100 * ih) / ch * 100,
                "width": (m["w"] / 100 * iw) / cw * 100,
                "height": (m["h"] / 100 * ih) / ch * 100,
                "start": 0.0,
                "end": 1.0,
                "clip_a": a,
                "clip_b": b,
            })

    return regions


def _settings_for_clip(path, timeline_offset):
    """Watermark settings with clicked marks merged in for one clip."""
    if not _watermark:
        return None

    marks = _watermark.get("marks") or []
    if not marks:
        return _watermark

    merged = dict(_watermark)
    merged["regions"] = list(_watermark["regions"]) + _mark_regions(
        path, marks, timeline_offset
    )
    return merged


# --------------------------------------------------------------------------
# Filter building
# --------------------------------------------------------------------------

def _pixels(region, mode):
    cw, ch = _export["width"], _export["height"]

    w = min(cw, _even(cw * region["width"] / 100))
    h = min(ch, _even(ch * region["height"] / 100))
    x = max(0, min(cw - w, _even(cw * region["x"] / 100)))
    y = max(0, min(ch - h, _even(ch * region["y"] / 100)))

    if mode == "Inpaint":
        # delogo needs the box strictly inside the frame
        x = max(1, min(cw - w - 1, x))
        y = max(1, min(ch - h - 1, y))
        w = min(w, cw - x - 1)
        h = min(h, ch - y - 1)

    return x, y, w, h


def _enable(region, src_duration, src_start):
    """Time window in clip time (t restarts at 0 for every segment)."""
    if "clip_a" in region or "clip_b" in region:
        a, b = region.get("clip_a"), region.get("clip_b")
        if a is None and b is None:
            return "1"
        if a is None:
            return f"lt(t,{b:.3f})"
        if b is None:
            return f"gte(t,{a:.3f})"
        return f"gte(t,{a:.3f})*lt(t,{b:.3f})"

    if region["start"] <= 0 and region["end"] >= 1:
        return "1"
    a = region["start"] * src_duration - src_start
    b = region["end"] * src_duration - src_start
    return f"between(t,{a:.3f},{b:.3f})"


def _watermark_filter(settings, src_duration, src_start):
    if not settings or not settings["regions"]:
        return "[base]null[v];"

    mode = settings["mode"]
    regions = settings["regions"]
    chain = ""
    current = "base"

    for i, region in enumerate(regions):
        out = "v" if i == len(regions) - 1 else f"w{i}"
        x, y, w, h = _pixels(region, mode)
        en = f"enable='{_enable(region, src_duration, src_start)}'"

        if mode == "Cover":
            chain += (
                f"[{current}]drawbox=x={x}:y={y}:w={w}:h={h}:"
                f"color=black@1.0:t=fill:{en}[{out}];"
            )

        elif mode == "Inpaint":
            chain += (
                f"[{current}]delogo=x={x}:y={y}:w={w}:h={h}:{en}[{out}];"
            )

        elif mode == "Blur":
            radius = max(1, min(20, min(w, h) // 4))
            chain += (
                f"[{current}]split[m{i}][p{i}];"
                f"[p{i}]crop={w}:{h}:{x}:{y},boxblur={radius}:2[b{i}];"
                f"[m{i}][b{i}]overlay={x}:{y}:{en}[{out}];"
            )

        else:  # Pixelate
            sw = max(2, _even(w / 16))
            sh = max(2, _even(h / 16))
            chain += (
                f"[{current}]split[m{i}][p{i}];"
                f"[p{i}]crop={w}:{h}:{x}:{y},"
                f"scale={sw}:{sh}:flags=neighbor,"
                f"scale={w}:{h}:flags=neighbor[b{i}];"
                f"[m{i}][b{i}]overlay={x}:{y}:{en}[{out}];"
            )

        current = out

    return chain


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _run(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg failed:\n" + (result.stderr[-5000:] or result.stdout[-5000:])
        )


def _render_segment(segment, destination, timeline_offset=0.0):
    """Returns False if the clip becomes empty after trimming."""
    ffmpeg = get_ffmpeg_path()
    info = get_video_info(segment.path)

    cw, ch = _export["width"], _export["height"]
    fps = int(_export["fps"])

    src_duration = float(info.get("duration_seconds") or 0.0)
    source_start = _source_start(segment)
    duration = float(segment.duration)

    if duration <= 0:
        raise ValueError("A timeline clip has an invalid duration.")

    # Trim the TikTok end card when this clip runs to the end of its source
    trim_end = _watermark["trim_end"] if _watermark else 0.0
    if trim_end > 0 and src_duration > 0:
        if source_start + duration >= src_duration - 0.1:
            duration -= trim_end
            if duration < 0.1:
                return False

    command = [ffmpeg, "-y", "-ss", str(source_start), "-i", str(segment.path)]

    use_source_audio = info.get("has_audio", False) and not _export["mute"]
    if use_source_audio:
        audio_input = "0:a"
    else:
        command += [
            "-f", "lavfi", "-t", str(duration),
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        audio_input = "1:a"

    settings = _settings_for_clip(segment.path, timeline_offset)

    video_filter = (
        f"[0:v]trim=duration={duration},"
        "setpts=PTS-STARTPTS,"
        f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
        f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={fps},format=yuv420p[base];"
        + _watermark_filter(settings, src_duration or 1.0, source_start)
    )
    audio_filter = (
        f"[{audio_input}]atrim=duration={duration},asetpts=PTS-STARTPTS[a]"
    )

    command += [
        "-filter_complex", video_filter + audio_filter,
        "-map", "[v]", "-map", "[a]",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", str(_export["preset"]),
        "-crf", str(_export["crf"]),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        str(destination),
    ]

    _run(command)
    return True


def export_timeline(segments, output_path, progress_callback=None):
    """Render clips, apply watermark removal, and join them."""
    if not segments:
        raise ValueError("The timeline is empty.")

    output_path = Path(output_path)
    if output_path.suffix.lower() != ".mp4":
        raise ValueError("Please export as an MP4 file.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    def report(value, message):
        if progress_callback:
            progress_callback(int(value), message)

    with tempfile.TemporaryDirectory(prefix="antony_video_editor_") as temp:
        temp_dir = Path(temp)
        rendered = []
        total = len(segments)
        offset = 0.0  # where each clip starts on the editor timeline

        for index, segment in enumerate(segments):
            report(
                (index / (total + 1)) * 90,
                f"Rendering clip {index + 1} of {total}…",
            )
            clip_output = temp_dir / f"clip_{index:04d}.mp4"
            if _render_segment(segment, clip_output, offset):
                rendered.append(clip_output)
            offset += float(segment.duration)

        if not rendered:
            raise ValueError("Nothing left to export after trimming.")

        report(92, "Joining clips…")

        concat_file = temp_dir / "concat.txt"
        with concat_file.open("w", encoding="utf-8") as file:
            for clip in rendered:
                safe = str(clip.resolve()).replace("\\", "/")
                safe = safe.replace("'", "'\\''")
                file.write(f"file '{safe}'\n")

        _run([
            get_ffmpeg_path(), "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ])

    report(100, "Done")
    return str(output_path)


def render_preview_frame(segment, source_time, output_path,
                         apply_effect=True, timeline_offset=0.0):
    """Render a single PNG frame, optionally with the watermark effect."""
    ffmpeg = get_ffmpeg_path()
    info = get_video_info(segment.path)

    cw, ch = _export["width"], _export["height"]
    src_duration = float(info.get("duration_seconds") or 0.0)
    source_start = _source_start(segment)

    settings = (
        _settings_for_clip(segment.path, timeline_offset)
        if apply_effect else None
    )

    # Effect windows are measured in clip time, so shift the timestamps
    # to where this frame sits inside its clip.
    clip_time = max(0.0, float(source_time) - source_start)

    video_filter = (
        f"[0:v]setpts=PTS-STARTPTS+{clip_time:.3f}/TB,"
        f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
        f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,format=yuv420p[base];"
        + _watermark_filter(settings, src_duration or 1.0, source_start)
    )

    # Drop the trailing ";" so the graph ends cleanly on [v]
    video_filter = video_filter.rstrip(";")

    _run([
        ffmpeg, "-y",
        "-ss", str(max(0.0, float(source_time))),
        "-i", str(segment.path),
        "-filter_complex", video_filter,
        "-map", "[v]",
        "-frames:v", "1",
        str(output_path),
    ])