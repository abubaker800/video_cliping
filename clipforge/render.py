"""Step 7: cut, crop or blur-fit, scale, burn captions, encode H.264 + AAC."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .config import ASPECT_RATIOS, FONTS_DIR, QUALITIES, ClipForgeError
from .ffmpeg_utils import detect_encoder, encoder_args, run_ffmpeg


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def output_size(aspect: str, quality: str, src_w: int, src_h: int) -> tuple[int, int, str]:
    """Output width/height. The quality tier is capped at the source's own tier (no upscaling
    past the source: a 1080p source never produces a 1440p or 4K file)."""
    ratio_w, ratio_h = ASPECT_RATIOS[aspect]
    wanted = QUALITIES[quality]
    short = min(wanted, min(src_w, src_h))
    note = ""
    if short < wanted:
        note = f"Source is {min(src_w, src_h)}p, so output is limited to {short}p instead of {quality}."
    if ratio_w <= ratio_h:
        width, height = short, short * ratio_h / ratio_w
    else:
        width, height = short * ratio_w / ratio_h, short
    return _even(width), _even(height), note


def _filter_graph(width: int, height: int, fit_blur: bool, ass_name: str | None, cwd: str) -> str:
    if fit_blur:
        bw, bh = _even(width / 4), _even(height / 4)
        graph = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=8:2,scale={width}:{height}[bgb];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    else:
        graph = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                 f"crop={width}:{height},setsar=1")
    if ass_name:
        graph += f",ass=f={ass_name}"
        if any(p.suffix.lower() in (".ttf", ".otf", ".ttc") for p in FONTS_DIR.iterdir()):
            rel = os.path.relpath(FONTS_DIR, cwd).replace("\\", "/")
            if not any(ch in rel for ch in ":'[],;"):
                graph += f":fontsdir={rel}"
    return graph + ",format=yuv420p[v]"


def render_clip(src: str, start: float, duration: float, out_path: Path, width: int, height: int,
                fit_blur: bool, ass_path: Path | None, has_audio: bool,
                on_progress: Callable[[float], None], log: Callable[[str], None]) -> str:
    """Render one clip. Returns the encoder that was used."""
    # ffmpeg runs inside the .ass file's folder so the filter gets a plain file name
    # (no path escaping problems with spaces, quotes or Windows drive letters).
    cwd = str(ass_path.parent if ass_path else out_path.parent)
    graph = _filter_graph(width, height, fit_blur, ass_path.name if ass_path else None, cwd)
    base = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src,
            "-filter_complex", graph, "-map", "[v]"]
    audio = ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"] if has_audio else []
    tail = ["-movflags", "+faststart", str(out_path)]

    encoder = detect_encoder()
    try:
        run_ffmpeg(base + encoder_args(encoder, width, height) + audio + tail, duration, on_progress, cwd)
    except ClipForgeError as exc:
        if encoder == "libx264":
            raise
        log(f"Hardware encoder {encoder} failed for {out_path.name}; retrying on CPU. ({str(exc)[-160:]})")
        encoder = "libx264"
        run_ffmpeg(base + encoder_args(encoder, width, height) + audio + tail, duration, on_progress, cwd)
    return encoder
