# ClipForge

Turn long videos into short, captioned clips for TikTok, Reels and YouTube Shorts. Give it a video file or a URL. It finds the most engaging moments, adds styled captions and renders vertical clips.

It runs locally as a web app. Transcription happens on your machine with Whisper. Gemini picks the moments.

## Features

- **One-click pipeline:** extract audio, transcribe, find moments, rank them, caption them and render.
- **Speech transcription:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with word-level timestamps. It works in chunks and can resume.
- **Moment detection:** Google Gemini picks viral moments. It combines the transcript with loudness spikes such as shouts and laughs, and snaps clips to sentence boundaries.
- **Caption templates:** 6 built-in styles (Bold Pop, Karaoke, Minimal, Neon, Gaming, Boxed). Each is a JSON file you can edit or copy.
- **Multilingual:** 37 languages, including right-to-left and non-spaced scripts (Arabic, Urdu, Hebrew, Chinese, Japanese, Thai and others).
- **Flexible output:**
  - Aspect ratios: 9:16, 1:1, 4:5, 16:9
  - Quality: 720p, 1080p, 1440p, 4K
  - Fit mode: centre crop or blurred-background fit
  - Clip length: about 15s, 30s, 60s, 90s or a custom range
- **Input:** local `.mp4`, `.mkv`, `.mov` and `.webm` files (up to 10 hours), or a video URL via yt-dlp.

## Requirements

- Python 3.10 or newer
- [ffmpeg](https://ffmpeg.org/) (see Setup)
- A free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)
- An NVIDIA GPU is optional. It makes transcription much faster. On CPU the app uses the `small` Whisper model.

## Setup

```bash
git clone https://github.com/abubaker800/video_cliping.git
cd video_cliping

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Install ffmpeg if you don't have it:

```bash
brew install ffmpeg              # macOS
sudo apt install ffmpeg          # Debian/Ubuntu
winget install ffmpeg            # Windows
```

You can also point to a specific binary with `FFMPEG_PATH` in `.env`.

Create your config file and add your key:

```bash
cp .env.example .env
```

Then edit `.env` and set `GEMINI_API_KEY`.

## Usage

```bash
python app.py
```

Open http://127.0.0.1:7860 in your browser, then:

1. Choose a video file or paste a URL.
2. Pick the aspect ratio, quality, clip length, number of clips, language and caption template.
3. Click generate and watch the progress log.

Finished clips are saved in `output/`.

## Configuration

Set these in `.env`:

| Variable | Description |
| --- | --- |
| `GEMINI_API_KEY` | Your Gemini API key (required) |
| `GEMINI_MODEL` | Any Gemini text model your key can use |
| `GEMINI_MIN_INTERVAL` | Minimum seconds between Gemini calls, to stay under free-tier limits |
| `WHISPER_MODEL` | `tiny`, `base`, `small`, `medium` or `large-v3`. Empty means automatic (`small` on CPU, `large-v3` on an NVIDIA GPU) |
| `FFMPEG_PATH` | Full path to a specific ffmpeg binary. Empty means auto-detect |

## Custom caption templates

Templates live in `templates/` as JSON. Copy one, change the font, colours, size, position or animation, and it shows up in the app. Put `.ttf` or `.otf` files in `fonts/` and use the font's family name in the template's `"font"` field.

```json
{
  "name": "Karaoke",
  "font": "Arial",
  "size": 76,
  "colors": { "text": "#FFFFFF", "highlight": "#FF9F1C" },
  "animation": { "type": "karaoke" }
}
```

## Project layout

```
app.py              Gradio web app
clipforge/
  pipeline.py       Runs the 8 steps end to end
  transcribe.py     Whisper transcription
  energy.py         Loudness analysis
  moments.py        Gemini moment finding and ranking
  captions.py       Caption templates and subtitle building
  render.py         ffmpeg clip rendering
  download.py       URL downloads (yt-dlp)
templates/          Caption style JSON files and previews
fonts/              Optional custom fonts
output/             Rendered clips (created at runtime)
```

## Notes

Only download and clip videos you have the rights to use.
