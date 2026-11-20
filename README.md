<div align="center">

# 🎬 ClipForge

### Turn long videos into viral, captioned short clips — automatically.

Drop in a video or paste a link. ClipForge transcribes it, finds the most engaging moments with AI, adds styled captions and renders vertical clips ready for **TikTok, Instagram Reels and YouTube Shorts**.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Gradio](https://img.shields.io/badge/UI-Gradio-FF7C00?logo=gradio&logoColor=white)
![Whisper](https://img.shields.io/badge/Speech-faster--whisper-412991)
![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-4285F4?logo=google&logoColor=white)
![FFmpeg](https://img.shields.io/badge/Render-FFmpeg-007808?logo=ffmpeg&logoColor=white)
![Runs locally](https://img.shields.io/badge/Runs-locally-success)

</div>

<!--
  SCREENSHOTS: save your images in docs/ and uncomment this block.

<div align="center">
  <img src="docs/home.png" alt="ClipForge homepage" width="85%">
</div>
-->

---

## ✨ Features

| | |
|---|---|
| 🤖 **AI moment detection** | Gemini reads the transcript, spots hooks, punchlines and key insights, then ranks the best clips. |
| 🔊 **Loudness awareness** | Detects shouts, laughs and loud spikes so high-energy moments aren't missed. |
| 🎙️ **Accurate transcription** | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with word-level timestamps. Runs on your machine, resumable on long videos. |
| 💬 **Styled captions** | 6 built-in caption templates with word-by-word highlighting. Fully customizable with JSON. |
| 🌍 **37 languages** | Including right-to-left and non-spaced scripts: Arabic, Urdu, Hebrew, Hindi, Chinese, Japanese, Thai and more. |
| 📐 **Any format** | 9:16, 1:1, 4:5 or 16:9, from 720p up to 4K. |
| 🖼️ **Smart framing** | Centre crop, or blurred-background fit that keeps the whole frame. |
| 🔗 **Files or links** | Upload `.mp4` `.mkv` `.mov` `.webm` (up to 10 hours) or paste a URL. |

## 🎨 Caption Templates

<table>
  <tr>
    <td align="center"><img src="templates/previews/01_bold_pop.png" width="160"><br><b>Bold Pop</b><br><sub>Big text, yellow highlight</sub></td>
    <td align="center"><img src="templates/previews/02_karaoke.png" width="160"><br><b>Karaoke</b><br><sub>Words fill as spoken</sub></td>
    <td align="center"><img src="templates/previews/03_minimal.png" width="160"><br><b>Minimal</b><br><sub>Clean lower-third</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="templates/previews/04_neon.png" width="160"><br><b>Neon</b><br><sub>Glowing cyan and pink</sub></td>
    <td align="center"><img src="templates/previews/05_gaming.png" width="160"><br><b>Gaming</b><br><sub>Thick font, green highlight</sub></td>
    <td align="center"><img src="templates/previews/06_boxed.png" width="160"><br><b>Boxed</b><br><sub>Rounded dark box</sub></td>
  </tr>
</table>

## ⚙️ How It Works

```
 Video / URL
     │
     ▼
 1. Extract audio ──► 2. Transcribe (Whisper) ──► 3. Measure loudness
                                                        │
     ┌──────────────────────────────────────────────────┘
     ▼
 4. Find viral moments (Gemini) ──► 5. Rank clips
     │
     ▼
 6. Build captions ──► 7. Render with FFmpeg ──► 8. Save to output/
```

## 🚀 Getting Started

### Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/)
- A free [Gemini API key](https://aistudio.google.com/apikey)
- *(Optional)* An NVIDIA GPU for much faster transcription

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/abubaker800/video_cliping.git
cd video_cliping

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install FFmpeg (skip if already installed)
brew install ffmpeg                # macOS
sudo apt install ffmpeg            # Ubuntu / Debian
winget install ffmpeg              # Windows

# 5. Add your API key
cp .env.example .env
```

Open `.env` and set your key:

```env
GEMINI_API_KEY=your-key-here
```

### Run

```bash
python app.py
```

Then open **http://127.0.0.1:7860** in your browser.

## 🕹️ Usage

1. **Choose a video**: upload a file or paste a URL.
2. **Pick your settings**: aspect ratio, quality, clip length, number of clips, language and caption template.
3. **Click generate** and follow the live progress log.
4. **Grab your clips** from the `output/` folder.

## 🔧 Configuration

All settings live in `.env`:

| Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` | Your Gemini API key **(required)** | — |
| `GEMINI_MODEL` | Any Gemini text model your key can use | see `.env.example` |
| `GEMINI_MIN_INTERVAL` | Minimum seconds between Gemini calls (stays under free-tier limits) | `5` |
| `WHISPER_MODEL` | `tiny` · `base` · `small` · `medium` · `large-v3` | auto: `small` on CPU, `large-v3` on NVIDIA GPU |
| `FFMPEG_PATH` | Path to a specific FFmpeg binary | auto-detect |

## 🖌️ Create Your Own Caption Style

Templates are plain JSON files in `templates/`. Copy one, edit it, and it appears in the app automatically.

```json
{
  "name": "My Style",
  "description": "Each word fills with colour as it is spoken.",
  "font": "Arial",
  "bold": true,
  "size": 76,
  "colors": { "text": "#FFFFFF", "highlight": "#FF9F1C", "outline": "#1A1A2E" },
  "position": { "align": "bottom", "margin_v_percent": 22 },
  "max_words_per_line": 5,
  "animation": { "type": "karaoke", "fade_ms": 80 }
}
```

Want a custom font? Drop a `.ttf` or `.otf` file into `fonts/` and use its family name in `"font"`.

## 📁 Project Structure

```
ClipForge/
├── app.py                 # Gradio web interface
├── clipforge/
│   ├── pipeline.py        # Runs the full 8-step pipeline
│   ├── transcribe.py      # Whisper speech-to-text
│   ├── energy.py          # Loudness analysis
│   ├── moments.py         # Gemini moment finding and ranking
│   ├── gemini_client.py   # Gemini API wrapper
│   ├── captions.py        # Caption templates and subtitles
│   ├── render.py          # FFmpeg clip rendering
│   ├── download.py        # URL downloads (yt-dlp)
│   └── config.py          # Settings and language tables
├── templates/             # Caption styles and previews
├── fonts/                 # Optional custom fonts
└── output/                # Your finished clips
```

## 🛠️ Built With

[Gradio](https://www.gradio.app/) · [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [Google Gemini](https://ai.google.dev/) · [FFmpeg](https://ffmpeg.org/) · [yt-dlp](https://github.com/yt-dlp/yt-dlp) · [NumPy](https://numpy.org/)

## ⚠️ Disclaimer

Only download, clip and publish videos that you own or have permission to use.

---

<div align="center">

If ClipForge saves you time, consider giving it a ⭐

</div>
