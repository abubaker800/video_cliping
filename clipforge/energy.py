"""Step 3: loudness per second and the loudest spikes (shouts, laughs, explosions)."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def compute_loudness(wav_path: Path) -> np.ndarray:
    """Return loudness in dBFS for every second, reading the WAV one minute at a time."""
    values: list[float] = []
    with wave.open(str(wav_path), "rb") as wav:
        rate = wav.getframerate()
        while True:
            raw = wav.readframes(rate * 60)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            for i in range(0, len(samples), rate):
                sec = samples[i:i + rate]
                if len(sec) < rate // 4:
                    continue
                rms = float(np.sqrt(np.mean(sec * sec)))
                values.append(max(-80.0, 20.0 * np.log10(rms + 1e-9)))
    return np.array(values, dtype=np.float32)


def find_spikes(db: np.ndarray, max_per_hour: int = 30, min_gap: int = 8) -> list[dict]:
    """Seconds that stand out from the minute around them, strongest first."""
    n = len(db)
    if n < 5:
        return []
    half = 30
    prominence = np.zeros(n, dtype=np.float32)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        prominence[i] = db[i] - float(np.median(db[lo:hi]))
    floor = float(np.median(db))
    order = np.argsort(-prominence)
    limit = max(5, int(max_per_hour * n / 3600) + 1)
    chosen: list[int] = []
    for idx in order:
        i = int(idx)
        if prominence[i] < 6.0 or db[i] < floor:
            continue
        if any(abs(i - c) < min_gap for c in chosen):
            continue
        chosen.append(i)
        if len(chosen) >= limit:
            break
    return [{"time": float(i), "db": round(float(db[i]), 1),
             "prominence": round(float(prominence[i]), 1),
             "strength": round(float(min(1.0, prominence[i] / 20.0)), 2)} for i in chosen]


def quiet_point(db: np.ndarray, target: float, radius: int = 15) -> float:
    """Quietest second near target, so a Whisper chunk boundary does not cut a word."""
    n = len(db)
    if n == 0:
        return target
    lo, hi = max(0, int(target) - radius), min(n, int(target) + radius + 1)
    if lo >= hi:
        return target
    return float(lo + int(np.argmin(db[lo:hi]))) + 0.5
