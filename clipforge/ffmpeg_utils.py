"""Everything that talks to the ffmpeg / ffprobe binaries."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .config import ClipForgeError, env

_INSTALL_HELP = {
    "darwin": "Install it with:  brew install ffmpeg-full",
    "win32": "Install it with:  winget install Gyan.FFmpeg   (then reopen the terminal)",
    "linux": "Install it with:  sudo apt install ffmpeg",
}


def _install_help() -> str:
    for key, text in _INSTALL_HELP.items():
        if sys.platform.startswith(key):
            return text
    return "Install FFmpeg from https://ffmpeg.org/download.html"


def _has_ass_filter(ffmpeg: str) -> bool:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == ["ass"] for line in out.splitlines() if line.strip())


@lru_cache(maxsize=1)
def find_ffmpeg() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe) paths, preferring a build that can burn captions."""
    exe = ".exe" if sys.platform.startswith("win") else ""
    candidates: list[Path] = []
    if env("FFMPEG_PATH"):
        candidates.append(Path(env("FFMPEG_PATH")))
    for prefix in ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"):
        candidates.append(Path(prefix) / "ffmpeg")
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(Path(on_path))
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidates.append(Path(prefix) / "ffmpeg")

    existing = [c for c in candidates if c.is_file()]
    if not existing:
        raise ClipForgeError("FFmpeg was not found on this computer. " + _install_help())

    chosen = next((c for c in existing if _has_ass_filter(str(c))), None)
    if chosen is None:
        raise ClipForgeError(
            f"FFmpeg was found ({existing[0]}) but it was built without caption support "
            "(the 'ass' filter from libass is missing). " + _install_help()
        )
    probe = chosen.with_name("ffprobe" + exe)
    probe_path = str(probe) if probe.is_file() else shutil.which("ffprobe")
    if not probe_path:
        raise ClipForgeError("ffprobe was not found next to FFmpeg. " + _install_help())
    return str(chosen), probe_path


def probe(path: str) -> dict:
    """Read duration, size, fps and stream info without decoding the video."""
    _, ffprobe = find_ffmpeg()
    res = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise ClipForgeError(f"This file could not be read as a video: {res.stderr.strip()[-300:]}")
    try:
        info = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise ClipForgeError("ffprobe returned unreadable data for this file.") from exc

    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ClipForgeError("Unsupported file: it does not contain a video track.")

    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ClipForgeError("Unsupported file: the video duration could not be determined.")

    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation:
        rotation = int(float(video.get("tags", {}).get("rotate", 0) or 0))
    if abs(rotation) % 180 == 90:
        width, height = height, width

    fps = 30.0
    try:
        num, den = (video.get("avg_frame_rate") or "30/1").split("/")
        if float(den) > 0 and float(num) > 0:
            fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        pass

    return {"duration": duration, "width": width, "height": height, "fps": fps,
            "has_audio": audio is not None}


def run_ffmpeg(args: list[str], total_seconds: float | None = None,
               on_progress: Callable[[float], None] | None = None,
               cwd: str | None = None) -> None:
    """Run ffmpeg, streaming progress (0..1) to on_progress. Raises ClipForgeError on failure."""
    ffmpeg, _ = find_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats"] + args
    with tempfile.TemporaryFile() as err_file:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file, cwd=cwd, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            if on_progress and total_seconds and line.startswith("out_time_us="):
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, done / total_seconds)))
        proc.wait()
        if proc.returncode != 0:
            err_file.seek(0)
            tail = err_file.read().decode("utf-8", errors="replace").strip()[-1200:]
            raise ClipForgeError(f"FFmpeg failed:\n{tail}")


def extract_audio(src: str, dst: Path, duration: float,
                  on_progress: Callable[[float], None] | None = None) -> None:
    """Step 1: mono 16 kHz WAV, streamed straight to disk."""
    if dst.exists() and dst.stat().st_size > 1024:
        return
    tmp = dst.with_suffix(".part.wav")
    run_ffmpeg(["-i", src, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(tmp)], duration, on_progress)
    tmp.replace(dst)


@lru_cache(maxsize=1)
def detect_encoder() -> str:
    """Pick a hardware H.264 encoder that really works here, else libx264."""
    ffmpeg, _ = find_ffmpeg()
    if sys.platform == "darwin":
        order = ["h264_videotoolbox"]
    else:
        order = ["h264_nvenc", "h264_qsv", "h264_amf"]
    try:
        listed = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                                text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        listed = ""
    for enc in order:
        if enc not in listed:
            continue
        try:
            test = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=640x360:r=30", "-frames:v", "5",
                 "-pix_fmt", "yuv420p", "-c:v", enc, "-f", "null", "-"],
                capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if test.returncode == 0:
            return enc
    return "libx264"


def encoder_args(encoder: str, width: int, height: int) -> list[str]:
    bitrate = max(3_000_000, int(width * height * 4.8))
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    args = ["-c:v", encoder, "-b:v", str(bitrate), "-maxrate", str(int(bitrate * 1.5)),
            "-bufsize", str(bitrate * 2), "-pix_fmt", "yuv420p"]
    if encoder == "h264_videotoolbox":
        args += ["-allow_sw", "1", "-profile:v", "high"]
    return args


def check_disk_space(path: Path, needed_bytes: int) -> None:
    free = shutil.disk_usage(path).free
    if free < needed_bytes:
        raise ClipForgeError(
            f"Not enough disk space: about {needed_bytes / 1e9:.1f} GB is needed but only "
            f"{free / 1e9:.1f} GB is free on the drive holding {path}. Free up space and try again."
        )
