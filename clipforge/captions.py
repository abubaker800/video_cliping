"""Step 6: caption lines, translation, and .ass files styled by a JSON template."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .config import (NO_SPACE_LANGUAGES, RTL_LANGUAGES, TEMPLATES_DIR, ClipForgeError,
                     language_name, script_font)
from .gemini_client import Gemini, GeminiFormatError
from .moments import SENTENCE_END

_DEFAULTS = {
    "description": "",
    "font": "Arial",
    "font_overrides": {},
    "bold": True,
    "italic": False,
    "size": 80,
    "uppercase": False,
    "letter_spacing": 0,
    "colors": {"text": "#FFFFFF", "highlight": "#FFE600", "outline": "#000000",
               "shadow": "#000000", "box": "#101418E6", "glow": "#00E5FF",
               "glow_highlight": "#FF2FD0"},
    "outline": {"width": 5, "shadow": 0},
    "position": {"align": "bottom", "margin_v_percent": 20, "margin_h_percent": 6},
    "tilt": 0,
    "max_words_per_line": 3,
    "max_chars_per_line": 20,
    "animation": {"type": "highlight", "pop_scale": 100, "pop_ms": 90, "fade_ms": 0},
    "glow": {"enabled": False, "blur": 10, "width": 8},
    "box": {"enabled": False, "padding": 28},
}


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            out[key] = _merge(base[key], value)
        else:
            out[key] = value
    return out


def load_templates() -> dict[str, dict]:
    """Read every templates/*.json. Bad files are reported, not silently dropped."""
    templates: dict[str, dict] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ClipForgeError(f"Template file {path.name} is not valid JSON: {exc}") from exc
        tpl = _merge(_DEFAULTS, raw)
        tpl["name"] = str(raw.get("name") or path.stem)
        tpl["file"] = path
        if tpl["animation"]["type"] not in ("highlight", "karaoke", "none"):
            raise ClipForgeError(f"Template {path.name}: animation.type must be highlight, karaoke or none.")
        templates[tpl["name"]] = tpl
    if not templates:
        raise ClipForgeError(f"No caption templates found in {TEMPLATES_DIR}.")
    return templates


# -- building lines ----------------------------------------------------------
def build_lines(words: list[dict], clip_start: float, clip_end: float) -> list[dict]:
    """Phrase-sized lines with word timings relative to the clip start."""
    span = clip_end - clip_start
    rel = []
    for w in words:
        if w["e"] <= clip_start or w["s"] >= clip_end:
            continue
        s = max(0.0, w["s"] - clip_start)
        e = min(span, w["e"] - clip_start)
        if e - s >= 0.02:
            rel.append({"s": s, "e": e, "w": w["w"]})
    lines: list[dict] = []
    current: list[dict] = []
    for i, word in enumerate(rel):
        current.append(word)
        nxt = rel[i + 1] if i + 1 < len(rel) else None
        chars = sum(len(w["w"]) + 1 for w in current)
        if (nxt is None or word["w"][-1:] in SENTENCE_END or nxt["s"] - word["e"] > 0.6
                or len(current) >= 8 or chars >= 42):
            lines.append({"words": current})
            current = []
    _set_line_times(lines, span)
    return lines


def _set_line_times(lines: list[dict], span: float) -> None:
    for i, line in enumerate(lines):
        line["start"] = line["words"][0]["s"]
        limit = lines[i + 1]["words"][0]["s"] if i + 1 < len(lines) else span
        line["end"] = max(line["start"] + 0.1, min(line["words"][-1]["e"] + 0.25, limit))


def _tokens(text: str, lang: str) -> list[str]:
    if lang in NO_SPACE_LANGUAGES:
        return [ch for ch in text if not ch.isspace()]
    return text.split()


def translate_lines(gem: Gemini, lines: list[dict], source: str, target: str,
                    log: Callable[[str], None]) -> list[dict]:
    """Translate line by line. Each line keeps its start/end; words are re-timed inside it."""
    if not lines:
        return lines
    texts = [" ".join(w["w"] for w in line["words"]) for line in lines]
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    prompt = f"""Translate these video caption lines from {language_name(source)} to {language_name(target)}.

The lines are consecutive pieces of the same speech, so use the surrounding lines for context, but translate line by line:
- Return exactly {len(texts)} lines, in the same order. Never merge, split, drop or add lines.
- Keep each translation natural, spoken-style and about as short as the original.
- Do not add notes, numbering, quotes or transliteration.

LINES
{numbered}

Return STRICT JSON ONLY: {{"lines": ["translation of line 1", "translation of line 2"]}}"""

    def validate(data):
        if isinstance(data, dict):
            data = data.get("lines")
        if not isinstance(data, list) or len(data) != len(texts):
            got = len(data) if isinstance(data, list) else "no"
            raise ValueError(f'"lines" must be a list of exactly {len(texts)} strings (got {got})')
        if not all(isinstance(x, str) for x in data):
            raise ValueError('every item in "lines" must be a string')
        return data

    try:
        translated = gem.generate_json(prompt, validate)
    except GeminiFormatError as exc:
        log(f"Translation reply was unusable ({exc}); keeping the original language for this clip.")
        return lines

    out = []
    for line, text, original in zip(lines, translated, texts):
        tokens = _tokens(text.strip() or original, target)
        if not tokens:
            continue
        first, last = line["words"][0]["s"], line["words"][-1]["e"]
        weights = [len(t) + 1 for t in tokens]
        total, acc, words = float(sum(weights)), 0.0, []
        for token, weight in zip(tokens, weights):
            s = first + (last - first) * acc / total
            acc += weight
            words.append({"s": s, "e": first + (last - first) * acc / total, "w": token})
        out.append({"words": words, "start": line["start"], "end": line["end"]})
    return out


def split_for_template(lines: list[dict], tpl: dict, lang: str) -> list[dict]:
    """Break phrase lines into the short on-screen lines the template wants."""
    no_space = lang in NO_SPACE_LANGUAGES
    max_chars = int(tpl["max_chars_per_line"])
    if no_space:
        max_chars, max_words = max(4, int(max_chars * 0.6)), 10 ** 6
    else:
        max_words = int(tpl["max_words_per_line"])
    out: list[dict] = []
    for line in lines:
        current: list[dict] = []
        chars = 0
        for word in line["words"]:
            add = len(word["w"]) + (0 if no_space else 1)
            if current and (len(current) >= max_words or chars + add > max_chars + 1):
                out.append({"words": current})
                current, chars = [], 0
            current.append(word)
            chars += add
        if current:
            out.append({"words": current})
    span = max((l["end"] for l in lines), default=0.0)
    _set_line_times(out, span)
    return out


# -- ASS writing -------------------------------------------------------------
def ass_color(value: str) -> tuple[str, str]:
    """'#RRGGBB' or '#RRGGBBAA' -> ('BBGGRR', ass alpha 'AA' where 00 is opaque)."""
    v = value.strip().lstrip("#")
    if len(v) not in (6, 8):
        raise ClipForgeError(f"Bad colour '{value}' in a caption template (use #RRGGBB or #RRGGBBAA).")
    try:
        r, g, b = v[0:2], v[2:4], v[4:6]
        alpha = 255 - int(v[6:8], 16) if len(v) == 8 else 0
        int(v[:6], 16)
    except ValueError as exc:
        raise ClipForgeError(f"Bad colour '{value}' in a caption template.") from exc
    return f"{b}{g}{r}".upper(), f"{alpha:02X}"


def _style_color(value: str) -> str:
    bgr, alpha = ass_color(value)
    return f"&H{alpha}{bgr}"


def _c(value: str) -> str:
    return f"&H{ass_color(value)[0]}&"


def _time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def font_for(tpl: dict, lang: str) -> str:
    return tpl["font_overrides"].get(lang) or script_font(lang) or tpl["font"]


def _text(words: list[str], current: int | None, on: str, off: str, joiner: str) -> str:
    """Join words; wrap the current word in `on` tags and switch back with `off`."""
    parts = []
    for i, word in enumerate(words):
        if i == current and on:
            parts.append("{" + on + "}" + word + "{" + off + "}")
        else:
            parts.append(word)
    return joiner.join(parts)


def write_ass(path: Path, lines: list[dict], tpl: dict, width: int, height: int, lang: str) -> None:
    scale = min(width, height) / 1080.0
    colors, anim = tpl["colors"], dict(tpl["animation"])
    rtl = lang in RTL_LANGUAGES
    mode = anim["type"]
    if mode == "karaoke" and rtl:
        mode = "highlight"  # sweep fills run left-to-right, wrong for RTL; highlight word by word
    joiner = "" if lang in NO_SPACE_LANGUAGES else " "

    size = max(8, round(tpl["size"] * scale))
    border = round(tpl["outline"]["width"] * scale, 1)
    shadow = round(tpl["outline"]["shadow"] * scale, 1)
    align = {"bottom": 2, "middle": 5, "top": 8}.get(tpl["position"]["align"], 2)
    margin_v = round(height * tpl["position"]["margin_v_percent"] / 100)
    margin_h = round(width * tpl["position"]["margin_h_percent"] / 100)

    primary = colors["highlight"] if mode == "karaoke" else colors["text"]
    style = ",".join(str(x) for x in [
        "Default", font_for(tpl, lang), size, _style_color(primary), _style_color(colors["text"]),
        _style_color(colors["outline"]), _style_color(colors["shadow"]),
        -1 if tpl["bold"] else 0, -1 if tpl["italic"] else 0, 0, 0, 100, 100,
        round(tpl["letter_spacing"] * scale, 1), tpl["tilt"], 1, border, shadow, align,
        margin_h, margin_h, margin_v, -1 if rtl else 1,
    ])

    pop = int(anim.get("pop_scale", 100))
    pop_on = f"\\t(0,{int(anim.get('pop_ms', 90))},\\fscx{pop}\\fscy{pop})" if pop != 100 else ""
    pop_off = "\\fscx100\\fscy100" if pop != 100 else ""
    fade = int(anim.get("fade_ms", 0))

    # Underlays are copies of the text drawn beneath it: a fat dark outline makes the
    # rounded box, a blurred coloured outline makes the glow.
    underlays: list[tuple[str, str, str]] = []  # (leading tags, current-word on, off)
    if tpl["box"]["enabled"]:
        bgr, alpha = ass_color(colors["box"])
        pad = round(tpl["box"]["padding"] * scale, 1)
        underlays.append((f"\\1c&H{bgr}&\\3c&H{bgr}&\\1a&H{alpha}&\\3a&H{alpha}&"
                          f"\\bord{pad}\\shad0\\blur1", pop_on, pop_off))
    if tpl["glow"]["enabled"]:
        lead = (f"\\1a&HFF&\\3c{_c(colors['glow'])}\\bord{round(tpl['glow']['width'] * scale, 1)}"
                f"\\blur{round(tpl['glow']['blur'] * scale, 1)}\\shad0")
        underlays.append((lead, f"\\3c{_c(colors['glow_highlight'])}" + pop_on,
                          f"\\3c{_c(colors['glow'])}" + pop_off))
    top_layer = len(underlays)

    events: list[str] = []

    def add(layer: int, start: float, end: float, text: str) -> None:
        if end - start >= 0.01:
            events.append(f"Dialogue: {layer},{_time(start)},{_time(end)},Default,,0,0,0,,{text}")

    for line in lines:
        raw = [w["w"].upper() if tpl["uppercase"] else w["w"] for w in line["words"]]
        words = [_escape(w) for w in raw]
        if mode == "highlight":
            for i, word in enumerate(line["words"]):
                start = line["start"] if i == 0 else word["s"]
                end = line["words"][i + 1]["s"] if i + 1 < len(words) else line["end"]
                lead = f"\\fad({fade},0)" if fade and i == 0 else ""
                for layer, (u_lead, u_on, u_off) in enumerate(underlays):
                    add(layer, start, end, "{" + u_lead + lead + "}" + _text(words, i, u_on, u_off, joiner))
                on = f"\\c{_c(colors['highlight'])}" + pop_on
                off = f"\\c{_c(colors['text'])}" + pop_off
                add(top_layer, start, end, ("{" + lead + "}" if lead else "") + _text(words, i, on, off, joiner))
        else:
            lead = f"\\fad({fade},{fade})" if fade else ""
            plain = joiner.join(words)
            for layer, (u_lead, _, _) in enumerate(underlays):
                add(layer, line["start"], line["end"], "{" + u_lead + lead + "}" + plain)
            if mode == "karaoke":
                parts, done_cs = [], 0
                for i, word in enumerate(line["words"]):
                    nxt = line["words"][i + 1]["s"] if i + 1 < len(words) else line["end"]
                    upto = max(done_cs, int(round((nxt - line["start"]) * 100)))
                    parts.append(f"{{\\kf{upto - done_cs}}}{words[i]}")
                    done_cs = upto
                text = ("{" + lead + "}" if lead else "") + joiner.join(parts)
            else:
                text = ("{" + lead + "}" if lead else "") + plain
            add(top_layer, line["start"], line["end"], text)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
