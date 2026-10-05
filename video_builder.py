"""Generate an overlay video track from GPX telemetry, optionally composited over a source video."""
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed

from gpx_parser import load_gpx
from overlay_renderer import DEFAULT_THEME, build_widgets, render_frame

WORKER_COUNT = max(1, multiprocessing.cpu_count() - 1)

DEFAULT_CANVAS = (1920, 1080)

RESOLUTIONS = {
    "HD (720p)": 720,
    "Full HD (1080p)": 1080,
    "4K (2160p)": 2160,
}
DEFAULT_RESOLUTION = "Full HD (1080p)"

ASPECT_RATIOS = {
    "16:9 (Landscape)": (16, 9),
    "9:16 (Portrait)": (9, 16),
}
DEFAULT_ASPECT = "16:9 (Landscape)"

# label -> (numeric fps used for frame-time math, exact rational string passed to ffmpeg)
FPS_PRESETS = {
    "23.976": (24000 / 1001, "24000/1001"),
    "24": (24.0, "24"),
    "25": (25.0, "25"),
    "29.97": (30000 / 1001, "30000/1001"),
    "30": (30.0, "30"),
    "48": (48.0, "48"),
    "50": (50.0, "50"),
    "59.94": (60000 / 1001, "60000/1001"),
    "60": (60.0, "60"),
    "120": (120.0, "120"),
}
FPS_OPTIONS = list(FPS_PRESETS.keys())
DEFAULT_FPS = "30"


def resolve_fps(fps_label):
    """Accepts either a FPS_PRESETS label or a raw numeric fps; returns (numeric_fps, ffmpeg_rate_str)."""
    if fps_label in FPS_PRESETS:
        return FPS_PRESETS[fps_label]
    numeric = float(fps_label)
    return numeric, repr(numeric)


def target_dimensions(resolution_key, aspect_key):
    """Resolve a resolution+aspect preset to an even (w, h) pixel size."""
    short_edge = RESOLUTIONS.get(resolution_key, RESOLUTIONS[DEFAULT_RESOLUTION])
    ar_w, ar_h = ASPECT_RATIOS.get(aspect_key, ASPECT_RATIOS[DEFAULT_ASPECT])

    if ar_w >= ar_h:
        h = short_edge
        w = round(h * ar_w / ar_h)
    else:
        w = short_edge
        h = round(w * ar_h / ar_w)

    w += w % 2
    h += h % 2
    return w, h


def _resource_dir():
    """Directory containing bundled binaries when frozen by py2app, else None."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "..", "Resources", "bin")
    return None


def _find_binary(name):
    res_dir = _resource_dir()
    if res_dir:
        candidate = os.path.join(res_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return name


FFMPEG = _find_binary("ffmpeg")
FFPROBE = _find_binary("ffprobe")

_hw_encoder_cache = {}


def _has_encoder(name):
    if name not in _hw_encoder_cache:
        try:
            out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                                  check=True, capture_output=True, text=True).stdout
            _hw_encoder_cache[name] = name in out
        except Exception:
            _hw_encoder_cache[name] = False
    return _hw_encoder_cache[name]


class RenderCancelled(Exception):
    pass


def _run_cancelable(cmd, cancel_event, duration_s=None, progress_cb=None):
    """Run cmd, polling cancel_event so a cancel request can kill ffmpeg promptly instead of
    waiting for a potentially very long encode to finish on its own.

    stderr is discarded and stdout is drained continuously: leaving either as an unread PIPE
    deadlocks the child once it fills the OS pipe buffer (ffmpeg logs enough progress output
    over a long encode to hit this within minutes). If duration_s/progress_cb are given, cmd
    is expected to include "-progress pipe:1" so stdout carries parseable out_time_ms=... lines.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1)
    try:
        for line in proc.stdout:
            if duration_s and progress_cb and line.startswith("out_time_ms="):
                try:
                    out_ms = int(line.strip().split("=", 1)[1])
                    progress_cb(min(1.0, max(0.0, (out_ms / 1_000_000) / duration_s)))
                except ValueError:
                    pass
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.stdout.close()
                proc.wait()
                raise RenderCancelled()
        proc.wait()
    finally:
        if proc.stdout and not proc.stdout.closed:
            proc.stdout.close()
    return proc.returncode


def _run_ffmpeg_with_fallback(hw_cmd, sw_cmd, cancel_event=None, duration_s=None, progress_cb=None):
    """Try a hardware-accelerated ffmpeg command; fall back to the software command on failure."""
    if hw_cmd is not None:
        returncode = _run_cancelable(hw_cmd, cancel_event, duration_s, progress_cb)
        if returncode == 0:
            return
    returncode = _run_cancelable(sw_cmd, cancel_event, duration_s, progress_cb)
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, sw_cmd)


def probe_video(path):
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-of", "json", path,
    ]
    out = subprocess.check_output(cmd)
    info = json.loads(out)["streams"][0]
    width = int(info["width"])
    height = int(info["height"])
    num, den = info["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    duration = float(info.get("duration", 0) or 0)
    if duration <= 0:
        cmd2 = [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
        out2 = subprocess.check_output(cmd2)
        duration = float(json.loads(out2)["format"]["duration"])
    return width, height, fps, duration


_worker_state = {}


def _worker_init(gpx_path, widget_config, theme):
    """Runs once per worker process: parse GPX and build widgets a single time,
    then reuse them for every frame that process renders."""
    telemetry = load_gpx(gpx_path)
    widgets = build_widgets(widget_config, telemetry, theme=theme)
    _worker_state["telemetry"] = telemetry
    _worker_state["widgets"] = widgets


def _render_frame_to_file(args):
    i, t, out_w, out_h, frame_path = args
    telemetry = _worker_state["telemetry"]
    widgets = _worker_state["widgets"]
    tp = telemetry.sample_at(t)
    frame = render_frame(widgets, tp, out_w, out_h)
    frame.save(frame_path)
    return i


def render_preview_frame(gpx_path, widget_config, theme=DEFAULT_THEME, canvas=DEFAULT_CANVAS,
                          elapsed_s=0.0, video_path=None):
    """Render a single RGBA overlay frame (for GUI preview) at elapsed_s seconds into the track."""
    telemetry = load_gpx(gpx_path)
    w, h = canvas
    if video_path and os.path.isfile(video_path):
        try:
            w, h, _, _ = probe_video(video_path)
        except Exception:
            pass
    widgets = build_widgets(widget_config, telemetry, theme=theme)
    elapsed_s = max(0.0, min(elapsed_s, telemetry.duration_s))
    tp = telemetry.sample_at(elapsed_s)
    frame = render_frame(widgets, tp, w, h)
    return frame, telemetry.duration_s


def render_overlay_video(gpx_path, out_path, widget_config, video_path=None,
                          theme=DEFAULT_THEME, resolution=DEFAULT_RESOLUTION,
                          aspect=DEFAULT_ASPECT, fps=DEFAULT_FPS, progress_cb=None,
                          cancel_event=None):
    """
    widget_config: {'speed': {'enabled': True, 'position': 'top-left', 'scale': 1.0, 'opacity': 0.55, 'unit': 'kmh'}, ...}
    video_path: optional source video to composite onto. If omitted, outputs a transparent
                alpha-channel .mov containing only the overlay widgets.
    resolution/aspect: output pixel size preset, resolved via target_dimensions().
    fps: output frame rate; overlay frames are generated at this rate too.
    progress_cb: optional callable(stage: str, fraction: float)
    cancel_event: optional threading.Event; when set, the render stops and raises RenderCancelled.
    """
    telemetry = load_gpx(gpx_path)
    widgets = build_widgets(widget_config, telemetry, theme=theme)
    if not widgets:
        raise ValueError("No overlay widgets enabled")

    out_w, out_h = target_dimensions(resolution, aspect)
    fps_numeric, fps_rate_str = resolve_fps(fps)

    if video_path:
        _src_w, _src_h, _src_fps, vid_duration = probe_video(video_path)
        duration = min(vid_duration, telemetry.duration_s) if telemetry.duration_s > 0 else vid_duration
    else:
        duration = telemetry.duration_s

    if duration <= 0:
        raise ValueError("Could not determine render duration from the GPX track")

    n_frames = max(1, int(duration * fps_numeric))

    with tempfile.TemporaryDirectory() as tmpdir:
        tasks = []
        for i in range(n_frames):
            frame_path = os.path.join(tmpdir, f"frame_{i:06d}.png")
            tasks.append((i, i / fps_numeric, out_w, out_h, frame_path))

        done = 0
        with ProcessPoolExecutor(
            max_workers=WORKER_COUNT,
            initializer=_worker_init,
            initargs=(gpx_path, widget_config, theme),
        ) as pool:
            futures = [pool.submit(_render_frame_to_file, task) for task in tasks]
            try:
                for future in as_completed(futures):
                    future.result()
                    done += 1
                    if progress_cb and done % 5 == 0:
                        progress_cb("rendering_overlay", done / n_frames)
                    if cancel_event is not None and cancel_event.is_set():
                        raise RenderCancelled()
            except RenderCancelled:
                for f in futures:
                    f.cancel()
                pool.shutdown(wait=True, cancel_futures=True)
                raise

        if progress_cb:
            progress_cb("rendering_overlay", 1.0)

        def encode_progress(fraction):
            if progress_cb:
                progress_cb("compositing", fraction)

        if not video_path:
            sw_cmd = [
                FFMPEG, "-y", "-progress", "pipe:1", "-nostats",
                "-framerate", fps_rate_str,
                "-i", os.path.join(tmpdir, "frame_%06d.png"),
                "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                out_path,
            ]
            hw_cmd = None
            if _has_encoder("prores_videotoolbox"):
                hw_cmd = [
                    FFMPEG, "-y", "-progress", "pipe:1", "-nostats",
                    "-framerate", fps_rate_str,
                    "-i", os.path.join(tmpdir, "frame_%06d.png"),
                    "-pix_fmt", "bgra",
                    "-c:v", "prores_videotoolbox", "-profile:v", "4444", "-allow_sw", "1",
                    out_path,
                ]
            encode_progress(0.0)
            _run_ffmpeg_with_fallback(hw_cmd, sw_cmd, cancel_event, duration, encode_progress)
            encode_progress(1.0)
            return out_path

        overlay_video_path = os.path.join(tmpdir, "overlay.mov")
        cmd_encode = [
            FFMPEG, "-y",
            "-framerate", fps_rate_str,
            "-i", os.path.join(tmpdir, "frame_%06d.png"),
            "-c:v", "qtrle",
            overlay_video_path,
        ]
        _run_cancelable(cmd_encode, cancel_event)

        encode_progress(0.0)

        # Scale source video to fill out_w x out_h preserving aspect, then letterbox/pillarbox
        # pad to exactly match, so the overlay (rendered at out_w x out_h) lines up pixel-for-pixel.
        scale_pad = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={fps_rate_str}"
        )
        filter_args = [
            "-filter_complex",
            f"[0:v]{scale_pad}[bg];[1:v]scale={out_w}:{out_h}[ov];[bg][ov]overlay=0:0:shortest=1[outv]",
            "-map", "[outv]", "-map", "0:a?",
            "-t", str(duration),
        ]
        sw_cmd = [
            FFMPEG, "-y", "-progress", "pipe:1", "-nostats",
            "-i", video_path, "-i", overlay_video_path, *filter_args,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "copy",
            out_path,
        ]
        hw_cmd = None
        if _has_encoder("h264_videotoolbox"):
            hw_cmd = [
                FFMPEG, "-y", "-progress", "pipe:1", "-nostats",
                "-i", video_path, "-i", overlay_video_path, *filter_args,
                "-c:v", "h264_videotoolbox", "-q:v", "65", "-allow_sw", "1",
                "-c:a", "copy",
                out_path,
            ]
        _run_ffmpeg_with_fallback(hw_cmd, sw_cmd, cancel_event, duration, encode_progress)

        encode_progress(1.0)

    return out_path
