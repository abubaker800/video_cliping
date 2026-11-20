"""Preview image for each caption template, drawn by FFmpeg with the real caption code."""
from __future__ import annotations

import subprocess

from .captions import load_templates, split_for_template, write_ass
from .config import PREVIEW_DIR
from .ffmpeg_utils import find_ffmpeg

_SAMPLE = ["This", "is", "how", "your", "captions", "will", "look"]
_W, _H = 540, 960


def _sample_lines() -> list[dict]:
    words = [{"s": 0.3 * i, "e": 0.3 * i + 0.28, "w": w} for i, w in enumerate(_SAMPLE)]
    return [{"words": words, "start": 0.0, "end": 0.3 * len(words)}]


def ensure_previews() -> list[tuple[str, str]]:
    """Create or refresh templates/previews/<name>.png. Returns [(image path, template name)]."""
    ffmpeg, _ = find_ffmpeg()
    gallery = []
    for name, tpl in load_templates().items():
        png = PREVIEW_DIR / f"{tpl['file'].stem}.png"
        if not png.exists() or png.stat().st_mtime < tpl['file'].stat().st_mtime:
            lines = split_for_template(_sample_lines(), tpl, "en")
            # Show the frame where the second word of the first line is active.
            at = lines[0]["words"][min(1, len(lines[0]["words"]) - 1)]["s"] + 0.2
            ass = PREVIEW_DIR / "_preview.ass"
            write_ass(ass, lines, tpl, _W, _H, "en")
            for source in (f"gradients=s={_W}x{_H}:c0=0x2B5876:c1=0x4E4376:x0=0:y0=0:x1={_W}:y1={_H}:d=4",
                           f"color=c=0x2B3A55:s={_W}x{_H}:d=4"):
                res = subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source,
                     "-vf", "ass=f=_preview.ass", "-ss", f"{at:.2f}", "-frames:v", "1", png.name],
                    cwd=str(PREVIEW_DIR), capture_output=True)
                if res.returncode == 0 and png.exists():
                    break
            ass.unlink(missing_ok=True)
        if png.exists():
            gallery.append((str(png), name))
    return gallery
