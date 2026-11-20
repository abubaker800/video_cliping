"""Step 2: faster-whisper in 10-minute chunks, word timestamps, resumable."""
from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from .config import CHUNK_SECONDS, SAMPLE_RATE, ClipForgeError, env
from .energy import quiet_point


def pick_device() -> tuple[str, str, str]:
    """Return (device, compute_type, model_name). GPU if CUDA is usable, else CPU + small."""
    device, compute, model = "cpu", "int8", "small"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            device, compute, model = "cuda", "float16", "large-v3"
    except Exception:
        pass
    return device, compute, env("WHISPER_MODEL") or model


def _load_model(device: str, compute: str, model_name: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ClipForgeError("faster-whisper is not installed. Run: pip install -r requirements.txt") from exc
    try:
        return WhisperModel(model_name, device=device, compute_type=compute)
    except Exception as exc:
        if device == "cuda":
            try:
                return WhisperModel(env("WHISPER_MODEL") or "small", device="cpu", compute_type="int8")
            except Exception:
                pass
        raise ClipForgeError(
            f"The Whisper model '{model_name}' could not be loaded ({exc}). The first run needs "
            "an internet connection to download the model."
        ) from exc


def _plan_chunks(duration: float, db: np.ndarray) -> list[float]:
    bounds = [0.0]
    t = float(CHUNK_SECONDS)
    while t < duration - 30:
        bounds.append(quiet_point(db, t))
        t += CHUNK_SECONDS
    bounds.append(float(duration))
    return [round(b, 2) for b in bounds]


def _read_chunk(wav_path: Path, start: float, end: float) -> np.ndarray:
    with wave.open(str(wav_path), "rb") as wav:
        wav.setpos(min(wav.getnframes(), int(start * SAMPLE_RATE)))
        raw = wav.readframes(int((end - start) * SAMPLE_RATE))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def transcribe(wav_path: Path, work: Path, duration: float, db: np.ndarray,
               on_progress: Callable[[float, str], None], log: Callable[[str], None]) -> dict:
    """Return {"language": code, "words": [{"s", "e", "w"}, ...]} with absolute times."""
    device, compute, model_name = pick_device()
    progress_path = work / "transcript_progress.json"
    bounds = _plan_chunks(duration, db)

    state = {"model": model_name, "bounds": bounds, "language": None, "done": {}}
    if progress_path.exists():
        try:
            saved = json.loads(progress_path.read_text(encoding="utf-8"))
            if saved.get("model") == model_name and saved.get("bounds") == bounds:
                state = saved
        except (json.JSONDecodeError, OSError):
            pass

    total = len(bounds) - 1
    todo = [i for i in range(total) if str(i) not in state["done"]]
    if len(todo) < total:
        log(f"Resuming transcription: {total - len(todo)} of {total} chunks already done.")

    if todo:
        log(f"Loading Whisper model '{model_name}' on {device.upper()} (first run downloads it)...")
        model = _load_model(device, compute, model_name)
        for n_done, i in enumerate(todo):
            start, end = bounds[i], bounds[i + 1]
            audio = _read_chunk(wav_path, start, end)
            base = (total - len(todo) + n_done) / total
            on_progress(base, f"chunk {i + 1}/{total}")
            words: list[dict] = []
            detected = None
            if len(audio) > SAMPLE_RATE // 2:
                segments, info = model.transcribe(
                    audio, language=state["language"], word_timestamps=True,
                    vad_filter=True, beam_size=5, condition_on_previous_text=False,
                )
                detected = info.language
                for seg in segments:
                    for w in seg.words or []:
                        text = w.word.strip()
                        if text:
                            words.append({"s": round(start + w.start, 2),
                                          "e": round(start + max(w.end, w.start + 0.05), 2),
                                          "w": text})
                    on_progress(base + min(1.0, seg.end / max(1.0, end - start)) / total,
                                f"chunk {i + 1}/{total}")
            if state["language"] is None and detected and len(words) >= 20:
                state["language"] = detected
                log(f"Detected spoken language: {detected}")
            state["done"][str(i)] = words
            state.setdefault("last_detected", detected)
            if detected:
                state["last_detected"] = detected
            _save(progress_path, state)
        del model

    words = [w for i in range(total) for w in state["done"][str(i)]]
    words.sort(key=lambda w: w["s"])
    language = state.get("language") or state.get("last_detected") or "en"
    on_progress(1.0, "done")
    return {"language": language, "words": words}
