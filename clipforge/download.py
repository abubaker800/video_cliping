"""Download a video from a YouTube (or other yt-dlp supported) link into the work folder."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .config import WORK_DIR, ClipForgeError
from .ffmpeg_utils import find_ffmpeg

DOWNLOAD_DIR = WORK_DIR / "downloads"
_URL_RE = re.compile(r"^https?://", re.I)


def is_url(text: str) -> bool:
    return bool(_URL_RE.match((text or "").strip()))


def download_video(url: str, max_height: int, progress: Callable[[float, str], None],
                   log: Callable[[str], None]) -> Path:
    """Download the best mp4 up to max_height. Reuses an earlier download of the same video."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise ClipForgeError("YouTube links need yt-dlp. Run:  pip install -U yt-dlp") from exc

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()[0]

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(d.get("downloaded_bytes", 0) / total, "downloading video")

    h = max_height
    audio = "ba[acodec^=mp4a]/ba[acodec=opus]"   # skip exotic audio FFmpeg cannot merge
    # (format, container) tried in order; a failed merge moves on to the next one.
    attempts = [
        (f"bv*[height<={h}][vcodec^=avc1]+{audio}", "mp4"),
        (f"bv*[height<={h}]+{audio}", "mkv"),
        (f"b[height<={h}][ext=mp4]/b[height<={h}]", "mp4"),
        (f"bv*[height<={h}]+ba/b", "mkv"),
    ]
    base = {
        "outtmpl": str(DOWNLOAD_DIR / "%(title).80B [%(id)s].%(ext)s"),
        "ffmpeg_location": str(Path(ffmpeg).parent),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "concurrent_fragment_downloads": 8,
        "http_chunk_size": 10 * 1024 * 1024,
        "no_warnings": True,
        "progress_hooks": [hook],
    }

    def clean(exc: Exception) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "")

    try:
        with yt_dlp.YoutubeDL({**base, "format": attempts[0][0]}) as ydl:
            info = ydl.extract_info(url.strip(), download=False)
        if info.get("is_live"):
            raise ClipForgeError("This is a live stream. Wait until it has ended, then try again.")
        vid = info.get("id")
        done = [p for p in DOWNLOAD_DIR.glob(f"*[[]{vid}[]].*")
                if p.suffix.lower() in (".mp4", ".mkv") and p.stat().st_size > 0]
        if done:
            log(f"Already downloaded: {done[0].name}")
            return done[0]
        log(f"Downloading: {info.get('title', url)}")
        last = None
        for fmt, container in attempts:
            try:
                with yt_dlp.YoutubeDL({**base, "format": fmt, "merge_output_format": container}) as ydl:
                    result = ydl.extract_info(url.strip(), download=True)
                path = Path((result.get("requested_downloads") or [{}])[0].get("filepath", ""))
                if path.is_file():
                    return path
                last = ClipForgeError("The download finished but the video file was not found.")
            except yt_dlp.utils.DownloadError as exc:
                last = exc
                log(f"Download attempt failed ({clean(exc)[:120]}). Trying another format...")
                for junk in DOWNLOAD_DIR.glob(f"*[[]{vid}[]].*"):
                    junk.unlink(missing_ok=True)
        raise last
    except ClipForgeError:
        raise
    except yt_dlp.utils.DownloadError as exc:
        raise ClipForgeError(
            f"Could not download that link: {clean(exc)}\n"
            "If it is private, age-restricted or region-locked it cannot be fetched. "
            "Updating yt-dlp (pip install -U yt-dlp) fixes most other failures.") from exc
