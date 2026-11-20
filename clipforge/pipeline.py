"""The one-click pipeline: audio -> transcript -> moments -> captions -> rendered clips."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import captions, energy, moments, render
from .config import (MAX_DURATION_SECONDS, OUTPUT_DIR, QUALITIES, SUPPORTED_EXTENSIONS, WORK_DIR,
                     ClipForgeError, language_name)
from .download import download_video, is_url
from .ffmpeg_utils import check_disk_space, detect_encoder, extract_audio, find_ffmpeg, probe
from .gemini_client import Gemini
from .transcribe import transcribe

# (label, share of total time)
STEPS = [
    ("Step 1/8 - Extracting audio", 0.04),
    ("Step 3/8 - Measuring loudness", 0.02),
    ("Step 2/8 - Transcribing speech", 0.46),
    ("Step 4/8 - Finding viral moments", 0.13),
    ("Step 5/8 - Ranking clips", 0.01),
    ("Step 6/8 - Building captions", 0.05),
    ("Step 7/8 - Rendering clips", 0.28),
    ("Step 8/8 - Saving results", 0.01),
]


def _eta(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


class _Progress:
    def __init__(self, report: Callable[[float, str], None]):
        self.report = report
        self.started = time.time()

    def step(self, index: int) -> Callable[[float, str], None]:
        before = sum(w for _, w in STEPS[:index])
        label, weight = STEPS[index]

        def update(fraction: float, detail: str = "") -> None:
            overall = min(0.999, before + weight * max(0.0, min(1.0, fraction)))
            elapsed = time.time() - self.started
            text = label + (f" ({detail})" if detail else "")
            if overall > 0.03 and elapsed > 8:
                text += f" - about {_eta(elapsed * (1 - overall) / overall)} left"
            self.report(overall, text)

        update(0.0)
        return update


def _slug(text: str, fallback: str = "clip") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:40].strip("-")
    return slug or fallback


def _post_text(title: str, description: str, hashtags: list[str]) -> str:
    tags = hashtags or moments.DEFAULT_HASHTAGS
    return "\n\n".join(part for part in (title, description, " ".join(tags)) if part)


def run_pipeline(opts: dict, report: Callable[[float, str], None],
                 log: Callable[[str], None]) -> dict:
    """opts: video_path, aspect, quality, fit_blur, min_len, max_len, n_clips,
    caption_lang (ISO code, "auto" or None for no captions), template (name)."""
    progress = _Progress(report)

    # -- checks before any heavy work ------------------------------------------
    raw = str(opts["video_path"]).strip().strip('"').strip("'")
    if is_url(raw):
        find_ffmpeg()
        src = download_video(raw, QUALITIES.get(opts["quality"], 1080),
                             lambda f, t: report(0.03 * f, t), log)
    else:
        src = Path(raw).expanduser()
    if not src.is_file():
        raise ClipForgeError(f"Video file not found: {src}")
    if src.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ClipForgeError(f"Unsupported file type '{src.suffix}'. Use one of: "
                             + ", ".join(SUPPORTED_EXTENSIONS))
    find_ffmpeg()
    info = probe(str(src))
    duration = info["duration"]
    if duration > MAX_DURATION_SECONDS + 60:
        raise ClipForgeError("This video is longer than 10 hours, which is the supported maximum.")
    if not info["has_audio"]:
        raise ClipForgeError("This video has no audio track, so there is nothing to analyse.")
    min_len, max_len = float(opts["min_len"]), float(opts["max_len"])
    if min_len < 3 or max_len <= min_len:
        raise ClipForgeError("Clip length: the minimum must be at least 3s and smaller than the maximum.")
    if duration < min_len:
        raise ClipForgeError(f"The video ({duration:.0f}s) is shorter than the minimum clip length.")
    max_len = min(max_len, duration)
    n_clips = int(opts["n_clips"])
    templates = captions.load_templates()
    tpl = templates.get(opts["template"]) or next(iter(templates.values()))

    stat = src.stat()
    key = hashlib.sha1(f"{src.resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode()).hexdigest()[:10]
    work = WORK_DIR / f"{_slug(src.stem, 'video')}-{key}"
    work.mkdir(parents=True, exist_ok=True)
    gem = Gemini(work / "gemini_cache", log)  # fails fast if the API key is missing

    width, height, size_note = render.output_size(opts["aspect"], opts["quality"],
                                                  info["width"], info["height"])
    wav = work / "audio.wav"
    need = int(n_clips * max_len * max(3_000_000, width * height * 4.8) / 8 * 1.4) + 200_000_000
    if not wav.exists():
        need += int(duration * 32000 * 1.05)
    check_disk_space(WORK_DIR, need)

    log(f"Video: {src.name} - {duration / 60:.1f} min, {info['width']}x{info['height']}")
    log(f"Output: {width}x{height} ({opts['aspect']}), encoder: {detect_encoder()}")
    if size_note:
        log(size_note)

    # -- Step 1: audio ---------------------------------------------------------
    update = progress.step(0)
    extract_audio(str(src), wav, duration, lambda f: update(f))

    # -- Step 3: loudness (done before step 2 so chunk cuts land on quiet moments)
    update = progress.step(1)
    db = energy.compute_loudness(wav)
    spikes = energy.find_spikes(db)
    log(f"Loudness: {len(spikes)} spikes found.")

    # -- Step 2: transcript ----------------------------------------------------
    update = progress.step(2)
    transcript = transcribe(wav, work, duration, db, update, log)
    words, spoken = transcript["words"], transcript["language"]
    log(f"Transcript: {len(words)} words, language '{spoken}'.")

    caption_lang = opts.get("caption_lang")
    if caption_lang == "auto":
        caption_lang = spoken
    text_lang = caption_lang or spoken

    # -- Step 4: viral moments -------------------------------------------------
    update = progress.step(3)
    sentences = moments.build_sentences(words)
    low_speech = len(words) < 30 or len(words) / (duration / 60) < 15
    candidates: list[dict] = []
    if low_speech:
        log("Very little speech detected: using loudness spikes to choose clips.")
    else:
        candidates = moments.find_candidates(gem, sentences, spikes, duration, min_len, max_len,
                                             n_clips, spoken, text_lang, update, log)
        log(f"Gemini proposed {len(candidates)} candidate moments.")

    # -- Step 5: rank ----------------------------------------------------------
    update = progress.step(4)
    chosen = moments.rank(candidates, n_clips)
    if len(chosen) < n_clips:
        before = len(chosen)
        extra = moments.spike_candidates(spikes, words, duration, min_len, max_len)
        chosen = moments.fill(chosen, extra, n_clips)
        if len(chosen) > before:
            log(f"Added {len(chosen) - before} clips from loudness spikes.")
    if not chosen:
        chosen = moments.even_candidates(duration, min_len, max_len, n_clips)
        log("No speech and no loud moments: falling back to evenly spaced clips.")
    chosen.sort(key=lambda c: -c["score"])
    log(f"Selected {len(chosen)} clips.")

    run_dir = OUTPUT_DIR / f"{_slug(src.stem, 'video')}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    ass_dir = work / "captions"
    ass_dir.mkdir(exist_ok=True)

    # -- Step 6: captions ------------------------------------------------------
    update = progress.step(5)
    ass_files: list[Path | None] = []
    for i, clip in enumerate(chosen):
        update(i / len(chosen), f"clip {i + 1}/{len(chosen)}")
        if not caption_lang:
            ass_files.append(None)
            continue
        lines = captions.build_lines(words, clip["start"], clip["end"])
        if lines and caption_lang != spoken:
            lines = captions.translate_lines(gem, lines, spoken, caption_lang, log)
        if not lines:
            ass_files.append(None)
            continue
        lines = captions.split_for_template(lines, tpl, caption_lang)
        ass = ass_dir / f"clip_{i + 1:02d}.ass"
        captions.write_ass(ass, lines, tpl, width, height, caption_lang)
        ass_files.append(ass)
    if caption_lang and caption_lang != spoken:
        log(f"Captions translated from {language_name(spoken)} to {language_name(caption_lang)}.")

    # -- Step 7: render --------------------------------------------------------
    update = progress.step(6)
    results = []
    for i, (clip, ass) in enumerate(zip(chosen, ass_files)):
        clip_len = clip["end"] - clip["start"]
        out = run_dir / f"clip_{i + 1:02d}_{_slug(clip['title'])}.mp4"
        render.render_clip(
            str(src), clip["start"], clip_len, out, width, height, bool(opts["fit_blur"]), ass,
            info["has_audio"],
            lambda f, i=i: update((i + f) / len(chosen), f"clip {i + 1}/{len(chosen)}"), log)
        title = clip["title"] or f"Clip {i + 1}"
        results.append({
            "index": i + 1, "file": str(out), "title": title, "score": clip["score"],
            "hook": clip["hook"], "reason": clip["reason"],
            "start": clip["start"], "end": clip["end"], "duration": round(clip_len, 2),
            "description": _post_text(title, clip["description"], clip["hashtags"]),
            "hashtags": clip["hashtags"] or moments.DEFAULT_HASHTAGS,
        })
        log(f"Rendered {out.name} (score {clip['score']}).")

    # -- Step 8: results -------------------------------------------------------
    update = progress.step(7)
    summary = {
        "source": str(src), "created": datetime.now().isoformat(timespec="seconds"),
        "spoken_language": spoken, "caption_language": caption_lang, "template": tpl["name"],
        "aspect": opts["aspect"], "resolution": f"{width}x{height}",
        "output_folder": str(run_dir), "clips": results,
    }
    (run_dir / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    report(1.0, "Done")
    log(f"Finished: {len(results)} clips saved in {run_dir}")
    return summary
