"""Gemini wrapper: rate limiting, retry with backoff, disk cache, JSON validation + repair."""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

from .config import ClipForgeError, env

MAX_ATTEMPTS = 6


class GeminiFormatError(Exception):
    """Gemini answered, but not with usable JSON, even after one repair attempt."""


def parse_json(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    if not text:
        raise ValueError("empty reply")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\[{].*[\]}]", text, flags=re.DOTALL)
        if not match:
            raise ValueError("reply is not JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc


class Gemini:
    def __init__(self, cache_dir: Path, log: Callable[[str], None]):
        self.api_key = env("GEMINI_API_KEY")
        if not self.api_key or self.api_key.startswith("paste-"):
            raise ClipForgeError(
                "Missing Gemini API key. Copy .env.example to .env and set GEMINI_API_KEY "
                "(free key: https://aistudio.google.com/apikey), then restart ClipForge."
            )
        self.model = env("GEMINI_MODEL", "gemini-3.6-flash")
        try:
            self.min_interval = float(env("GEMINI_MIN_INTERVAL", "5"))
        except ValueError:
            self.min_interval = 5.0
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log = log
        self._client = None
        self._last_call = 0.0

    # -- low level -----------------------------------------------------------
    def _ensure_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise ClipForgeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _call(self, prompt: str) -> str:
        from google.genai import errors, types

        client = self._ensure_client()
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4)
        delay = 5.0
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                response = client.models.generate_content(model=self.model, contents=prompt, config=config)
                return response.text or ""
            except errors.APIError as exc:
                code = getattr(exc, "code", None) or 0
                message = str(getattr(exc, "message", "") or exc)
                last_error = f"{code}: {message[:200]}"
                if code in (401, 403) or (code == 400 and "api key" in message.lower()):
                    raise ClipForgeError(
                        "Gemini rejected the API key. Check GEMINI_API_KEY in your .env file."
                    ) from exc
                if code == 404:
                    raise ClipForgeError(
                        f"Gemini does not know the model '{self.model}'. Set GEMINI_MODEL in .env "
                        "to a model your key can use."
                    ) from exc
                if code != 429 and code < 500:
                    raise ClipForgeError(f"Gemini request failed ({last_error}).") from exc
                hinted = re.search(r"retry(?:Delay| in)?['\"\s:]+(\d+(?:\.\d+)?)\s*s", message)
                sleep_for = max(delay, float(hinted.group(1)) + 1 if hinted else 0)
            except ClipForgeError:
                raise
            except Exception as exc:  # network hiccup, timeout
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                sleep_for = delay
            if attempt == MAX_ATTEMPTS:
                break
            sleep_for = min(120.0, sleep_for) + random.uniform(0, 2)
            self.log(f"Gemini busy ({last_error}). Retry {attempt}/{MAX_ATTEMPTS - 1} in {sleep_for:.0f}s...")
            time.sleep(sleep_for)
            delay *= 2
        raise ClipForgeError(
            f"Gemini kept failing after {MAX_ATTEMPTS} attempts ({last_error}). If this is a "
            "rate limit, wait a while and press Generate again: finished work is cached and "
            "will not be redone."
        )

    # -- public --------------------------------------------------------------
    def generate_json(self, prompt: str, validator: Callable[[Any], Any]) -> Any:
        """Return validator(parsed reply). Cached on disk; one repair retry on bad JSON."""
        key = hashlib.sha256(f"{self.model}\n{prompt}".encode("utf-8")).hexdigest()[:32]
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                return validator(parse_json(cached["response"]))
            except (ValueError, KeyError, OSError):
                pass

        text = self._call(prompt)
        try:
            result = validator(parse_json(text))
        except ValueError as first_error:
            self.log(f"Gemini returned malformed JSON ({first_error}); asking it to repair once.")
            repair = (
                f"{prompt}\n\n---\nYour previous reply was rejected: {first_error}.\n"
                f"Previous reply (truncated):\n{text[:6000]}\n\n"
                "Reply again with ONLY valid JSON that exactly matches the required schema. "
                "No markdown, no comments, no extra text."
            )
            text = self._call(repair)
            try:
                result = validator(parse_json(text))
            except ValueError as second_error:
                raise GeminiFormatError(str(second_error)) from second_error

        cache_file.write_text(
            json.dumps({"model": self.model, "prompt": prompt, "response": text}, ensure_ascii=False),
            encoding="utf-8")
        return result
