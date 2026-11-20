"""Paths, settings, language tables and the shared error type."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OUTPUT_DIR = ROOT / "output"
WORK_DIR = ROOT / "work"
TEMPLATES_DIR = ROOT / "templates"
PREVIEW_DIR = TEMPLATES_DIR / "previews"
FONTS_DIR = ROOT / "fonts"
for _d in (OUTPUT_DIR, WORK_DIR, PREVIEW_DIR, FONTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
MAX_DURATION_SECONDS = 10 * 3600

SAMPLE_RATE = 16000
CHUNK_SECONDS = 600          # Whisper chunk size (10 minutes)
WINDOW_SECONDS = 1200        # Gemini window size (20 minutes)
WINDOW_OVERLAP_SECONDS = 120

ASPECT_RATIOS = {"9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "16:9": (16, 9)}
QUALITIES = {"720p": 720, "1080p": 1080, "1440p": 1440, "4K": 2160}
CLIP_LENGTHS = {"15s": (8, 15), "30s": (18, 30), "60s": (35, 60), "90s": (60, 90)}
FIT_MODES = ("Centre crop", "Blurred background fit")

AUTO_LANGUAGE = "Auto (spoken language)"
NO_CAPTIONS = "No captions"

# name -> ISO code (codes match Whisper's language codes)
LANGUAGES = {
    "English": "en", "Urdu": "ur", "Hindi": "hi", "Arabic": "ar", "Spanish": "es",
    "French": "fr", "German": "de", "Portuguese": "pt", "Italian": "it",
    "Russian": "ru", "Turkish": "tr", "Indonesian": "id", "Malay": "ms",
    "Bengali": "bn", "Punjabi": "pa", "Persian": "fa", "Pashto": "ps",
    "Chinese (Simplified)": "zh", "Japanese": "ja", "Korean": "ko",
    "Vietnamese": "vi", "Thai": "th", "Dutch": "nl", "Polish": "pl",
    "Ukrainian": "uk", "Swedish": "sv", "Greek": "el", "Hebrew": "he",
    "Tamil": "ta", "Telugu": "te", "Marathi": "mr", "Gujarati": "gu",
    "Swahili": "sw", "Filipino": "tl", "Romanian": "ro", "Czech": "cs",
    "Hungarian": "hu",
}
LANGUAGE_NAMES = {code: name for name, code in LANGUAGES.items()}
RTL_LANGUAGES = {"ar", "ur", "fa", "ps", "he"}
NO_SPACE_LANGUAGES = {"zh", "ja", "th"}

# Fonts that ship with each OS and cover the script. (mac, windows, linux)
_SCRIPT_FONTS = {
    "ar": ("Geeza Pro", "Segoe UI", "Noto Sans Arabic"),
    "fa": ("Geeza Pro", "Segoe UI", "Noto Sans Arabic"),
    "ps": ("Geeza Pro", "Segoe UI", "Noto Sans Arabic"),
    "ur": ("Noto Nastaliq Urdu", "Urdu Typesetting", "Noto Nastaliq Urdu"),
    "he": ("Arial Hebrew", "Segoe UI", "Noto Sans Hebrew"),
    "hi": ("Kohinoor Devanagari", "Nirmala UI", "Noto Sans Devanagari"),
    "mr": ("Kohinoor Devanagari", "Nirmala UI", "Noto Sans Devanagari"),
    "bn": ("Kohinoor Bangla", "Nirmala UI", "Noto Sans Bengali"),
    "pa": ("Gurmukhi MN", "Nirmala UI", "Noto Sans Gurmukhi"),
    "gu": ("Kohinoor Gujarati", "Nirmala UI", "Noto Sans Gujarati"),
    "ta": ("Tamil Sangam MN", "Nirmala UI", "Noto Sans Tamil"),
    "te": ("Kohinoor Telugu", "Nirmala UI", "Noto Sans Telugu"),
    "zh": ("PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC"),
    "ja": ("Hiragino Sans", "Yu Gothic", "Noto Sans CJK JP"),
    "ko": ("Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans CJK KR"),
    "th": ("Thonburi", "Leelawadee UI", "Noto Sans Thai"),
}


def script_font(lang: str) -> str | None:
    """System font that can draw this language, or None if the template font is fine."""
    fonts = _SCRIPT_FONTS.get(lang)
    if not fonts:
        return None
    if sys.platform == "darwin":
        return fonts[0]
    if sys.platform.startswith("win"):
        return fonts[1]
    return fonts[2]


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


class ClipForgeError(Exception):
    """An error with a message that is safe and useful to show to the user."""
