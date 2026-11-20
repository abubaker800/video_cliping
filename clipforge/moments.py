"""Steps 4 and 5: ask Gemini for viral moments, snap them to sentences, rank them."""
from __future__ import annotations

import math
from typing import Callable

from .config import WINDOW_OVERLAP_SECONDS, WINDOW_SECONDS, language_name
from .gemini_client import Gemini, GeminiFormatError

SENTENCE_END = ".!?…۔؟।。！？"
DEFAULT_HASHTAGS = ["#shorts", "#viral", "#fyp"]


def build_sentences(words: list[dict]) -> list[dict]:
    """Group words into sentences using punctuation, pauses and a length cap."""
    sentences: list[dict] = []
    current: list[dict] = []
    for i, word in enumerate(words):
        current.append(word)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = word["w"][-1:] in SENTENCE_END or word["w"].rstrip("\"')”’")[-1:] in SENTENCE_END
        pause = nxt is not None and nxt["s"] - word["e"] > 0.9
        if nxt is None or ends or pause or len(current) >= 40:
            sentences.append({"start": current[0]["s"], "end": current[-1]["e"],
                              "text": " ".join(w["w"] for w in current)})
            current = []
    return sentences


def make_windows(duration: float) -> list[tuple[float, float]]:
    windows, start = [], 0.0
    step = WINDOW_SECONDS - WINDOW_OVERLAP_SECONDS
    while True:
        end = min(duration, start + WINDOW_SECONDS)
        windows.append((start, end))
        if end >= duration:
            return windows
        start += step


def _prompt(win: tuple[float, float], duration: float, sentences: list[dict], spikes: list[dict],
            min_len: float, max_len: float, want: int, spoken: str, text_lang: str) -> str:
    lines = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in sentences)
    spike_lines = "\n".join(f"{s['time']:.0f}s (strength {s['strength']})" for s in spikes) or "none"
    return f"""You are an expert short-form video editor who finds moments that go viral on TikTok, YouTube Shorts and Instagram Reels.

This is one window ({win[0]:.0f}s to {win[1]:.0f}s) of a video that is {duration:.0f}s long. Spoken language: {language_name(spoken)}.

TRANSCRIPT. Each line is one sentence: [start-end] text. Times are seconds from the start of the video.
{lines}

LOUDNESS SPIKES in this window (shouting, laughing, explosions, big reactions):
{spike_lines}

TASK
Pick up to {want} of the best standalone moments in this window.

RULES
- Prefer moments with a strong hook in the first 2 seconds, a clear payoff, surprise, humour, or a useful secret or tip.
- A loudness spike inside or just after a moment is a good sign of a reaction or payoff.
- Each clip must be understandable on its own by someone who has not seen the rest of the video.
- "start" must be the start time of a sentence and "end" must be the end time of a sentence, copied from the transcript.
- Each clip must be between {min_len:.0f} and {max_len:.0f} seconds long.
- Clips must not overlap each other.
- "score" is your honest virality estimate from 0 to 100. Do not give every clip a high score.
- "hook" is the opening sentence of the clip, quoted from the transcript.
- Write "title", "reason" and "description" in {language_name(text_lang)}. Title: under 70 characters, no hashtags.
- "description" is 1-2 sentences for the post caption. "hashtags" is 3 to 6 relevant hashtags, each starting with #.
- If nothing in this window is worth clipping, return an empty list.

Return STRICT JSON ONLY, with exactly this shape and no other text:
{{"candidates": [{{"start": 0.0, "end": 0.0, "score": 0, "hook": "", "reason": "", "title": "", "description": "", "hashtags": ["#tag"]}}]}}"""


def _validator(win: tuple[float, float]) -> Callable:
    def validate(data):
        if isinstance(data, dict):
            data = data.get("candidates")
        if not isinstance(data, list):
            raise ValueError('expected an object with a "candidates" list')
        good = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("every candidate must be an object")
            try:
                start, end = float(item["start"]), float(item["end"])
                score = int(round(float(item.get("score", 0))))
            except (KeyError, TypeError, ValueError):
                raise ValueError('every candidate needs numeric "start", "end" and "score"')
            if end <= start or start < win[0] - 5 or end > win[1] + 5:
                continue  # outside this window: drop quietly
            tags = item.get("hashtags") or []
            if isinstance(tags, str):
                tags = tags.split()
            tags = ["#" + str(t).strip().lstrip("#").replace(" ", "") for t in tags if str(t).strip()]
            good.append({
                "start": start, "end": end, "score": max(0, min(100, score)),
                "hook": str(item.get("hook") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "hashtags": tags[:8],
            })
        return good
    return validate


def snap(cand: dict, sentences: list[dict], min_len: float, max_len: float, duration: float) -> dict:
    """Move start/end onto sentence boundaries and force the length into [min_len, max_len]."""
    si = min(range(len(sentences)), key=lambda i: abs(sentences[i]["start"] - cand["start"]))
    later = range(si, len(sentences))
    ei = min(later, key=lambda i: abs(sentences[i]["end"] - cand["end"]))

    def length() -> float:
        return sentences[ei]["end"] - sentences[si]["start"]

    while length() > max_len and ei > si:
        ei -= 1
    while length() < min_len and ei + 1 < len(sentences) and \
            sentences[ei + 1]["end"] - sentences[si]["start"] <= max_len:
        ei += 1
    while length() < min_len and si > 0 and \
            sentences[ei]["end"] - sentences[si - 1]["start"] <= max_len:
        si -= 1

    start = max(0.0, sentences[si]["start"] - 0.15)
    end = min(duration, sentences[ei]["end"] + 0.3)
    if end - start > max_len:           # one very long sentence
        end = start + max_len
    if end - start < min_len:           # isolated speech: pad with what follows
        end = min(duration, start + min_len)
        start = max(0.0, end - min_len)
    out = dict(cand)
    out.update(start=round(start, 2), end=round(end, 2))
    if not out["hook"]:
        out["hook"] = sentences[si]["text"]
    return out


def find_candidates(gem: Gemini, sentences: list[dict], spikes: list[dict], duration: float,
                    min_len: float, max_len: float, n_clips: int, spoken: str, text_lang: str,
                    on_progress: Callable[[float, str], None], log: Callable[[str], None]) -> list[dict]:
    windows = make_windows(duration)
    want = max(3, min(10, math.ceil(n_clips * 1.5 / len(windows)) + 2))
    found: list[dict] = []
    for i, win in enumerate(windows):
        on_progress(i / len(windows), f"window {i + 1}/{len(windows)}")
        in_win = [s for s in sentences if s["end"] > win[0] and s["start"] < win[1]]
        if len(in_win) < 2:
            continue
        win_spikes = sorted((s for s in spikes if win[0] <= s["time"] <= win[1]), key=lambda s: s["time"])
        prompt = _prompt(win, duration, in_win, win_spikes, min_len, max_len, want, spoken, text_lang)
        try:
            cands = gem.generate_json(prompt, _validator(win))
        except GeminiFormatError as exc:
            log(f"Window {i + 1}: Gemini's reply was still malformed after a repair attempt ({exc}); skipped.")
            continue
        for cand in cands:
            found.append(snap(cand, sentences, min_len, max_len, duration))
    on_progress(1.0, "done")
    return found


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def spike_candidates(spikes: list[dict], words: list[dict], duration: float,
                     min_len: float, max_len: float) -> list[dict]:
    """Fallback for gameplay and other low-speech video: clips built around loud moments."""
    length = min(duration, (min_len + max_len) / 2)
    out = []
    for spike in spikes:
        start = max(0.0, spike["time"] - 0.4 * length)
        end = min(duration, start + length)
        start = max(0.0, end - length)
        said = " ".join(w["w"] for w in words if start <= w["s"] < start + 4)
        out.append({
            "start": round(start, 2), "end": round(end, 2),
            "score": int(50 + 45 * spike["strength"]),
            "hook": said,
            "reason": f"Loudness spike at {_mmss(spike['time'])} "
                      f"({spike['prominence']:.0f} dB above the surrounding minute).",
            "title": f"Big moment at {_mmss(spike['time'])}",
            "description": "", "hashtags": [],
        })
    return out


def even_candidates(duration: float, min_len: float, max_len: float, n: int) -> list[dict]:
    """Last resort when there is neither speech nor any loudness spike."""
    length = min(duration, (min_len + max_len) / 2)
    count = max(1, min(n, int(duration // length)))
    gap = (duration - count * length) / (count + 1)
    out = []
    for i in range(count):
        start = gap + i * (length + gap)
        out.append({"start": round(start, 2), "end": round(start + length, 2), "score": 30,
                    "hook": "", "reason": "No speech or loud moments found; evenly spaced clip.",
                    "title": f"Clip at {_mmss(start)}", "description": "", "hashtags": []})
    return out


def rank(candidates: list[dict], n: int) -> list[dict]:
    """Best score first, dropping anything that overlaps a better clip."""
    chosen: list[dict] = []
    for cand in sorted(candidates, key=lambda c: (-c["score"], c["start"])):
        if all(min(cand["end"], c["end"]) - max(cand["start"], c["start"]) <= 0.5 for c in chosen):
            chosen.append(cand)
        if len(chosen) >= n:
            break
    return chosen


def fill(chosen: list[dict], extra: list[dict], n: int) -> list[dict]:
    """Top up `chosen` with non-overlapping clips from `extra` (already strongest first)."""
    out = list(chosen)
    for cand in extra:
        if len(out) >= n:
            break
        if all(min(cand["end"], c["end"]) - max(cand["start"], c["start"]) <= 0.5 for c in out):
            out.append(cand)
    return out
