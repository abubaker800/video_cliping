"""ClipForge - local web app. Run:  python app.py   then open http://127.0.0.1:7860"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import traceback

import gradio as gr

from clipforge.captions import load_templates
from clipforge.config import (ASPECT_RATIOS, AUTO_LANGUAGE, CLIP_LENGTHS, FIT_MODES, LANGUAGES,
                              NO_CAPTIONS, OUTPUT_DIR, PREVIEW_DIR, QUALITIES,
                              SUPPORTED_EXTENSIONS, ClipForgeError)
from clipforge.pipeline import run_pipeline
from clipforge.previews import ensure_previews

CUSTOM = "Custom"


def startup() -> tuple[list[str], list[tuple[str, str]], str]:
    """Load templates and previews; collect any setup problem to show at the top of the page."""
    names, gallery, problem = [], [], ""
    try:
        names = list(load_templates().keys())
    except ClipForgeError as exc:
        problem = str(exc)
    try:
        gallery = ensure_previews()
    except ClipForgeError as exc:
        problem = problem or str(exc)
    return names, gallery, problem


def browse_for_video() -> str:
    """Open the operating system's file dialog. Returns '' if cancelled or unavailable."""
    try:
        if sys.platform == "darwin":
            script = ('POSIX path of (choose file with prompt "Choose a video for ClipForge" '
                      'of type {"mp4", "mkv", "mov", "webm", "public.movie"})')
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        elif sys.platform.startswith("win"):
            script = ("Add-Type -AssemblyName System.Windows.Forms; "
                      "$d = New-Object System.Windows.Forms.OpenFileDialog; "
                      "$d.Filter = 'Video files|*.mp4;*.mkv;*.mov;*.webm'; "
                      "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.FileName }")
            res = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                                 capture_output=True, text=True)
        else:
            res = subprocess.run(["zenity", "--file-selection", "--title=Choose a video"],
                                 capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else ""
    except OSError:
        return ""


def generate(path_text, uploaded, aspect, quality, fit_mode, length_choice, custom_min, custom_max,
             n_clips, language, template, progress=gr.Progress()):
    """Run the pipeline in a worker thread and stream its log and progress to the page."""
    video_path = (path_text or "").strip() or (uploaded or "")
    if not video_path:
        raise gr.Error("Choose a video first: paste a YouTube link, press Browse, paste a file path, or drop a file.")
    if length_choice == CUSTOM:
        min_len, max_len = float(custom_min or 0), float(custom_max or 0)
    else:
        min_len, max_len = CLIP_LENGTHS[length_choice]
    caption_lang = None if language == NO_CAPTIONS else (
        "auto" if language == AUTO_LANGUAGE else LANGUAGES[language])
    opts = {
        "video_path": video_path, "aspect": aspect, "quality": quality,
        "fit_blur": fit_mode == FIT_MODES[1], "min_len": min_len, "max_len": max_len,
        "n_clips": int(n_clips), "caption_lang": caption_lang, "template": template,
    }

    events: queue.Queue = queue.Queue()

    def worker():
        try:
            summary = run_pipeline(opts, lambda f, t: events.put(("progress", f, t)),
                                   lambda m: events.put(("log", m)))
            events.put(("done", summary))
        except ClipForgeError as exc:
            events.put(("error", str(exc)))
        except Exception as exc:  # unexpected: show it instead of hanging
            traceback.print_exc()
            events.put(("error", f"Unexpected error: {type(exc).__name__}: {exc}"))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    log_lines: list[str] = []
    current = "Starting..."

    def status() -> str:
        return "\n".join([current, "-" * 60] + log_lines[-200:])

    progress(0, desc=current)
    yield status(), gr.skip()
    while True:
        try:
            event = events.get(timeout=1.0)
        except queue.Empty:
            if not thread.is_alive() and events.empty():
                break
            continue
        kind = event[0]
        if kind == "progress":
            current = f"{event[1] * 100:.0f}%  {event[2]}"
            progress(event[1], desc=event[2])
            if not events.empty():
                continue  # skip redundant page updates when events pile up
            yield status(), gr.skip()
        elif kind == "log":
            log_lines.append(event[1])
            yield status(), gr.skip()
        elif kind == "error":
            current = "ERROR: " + event[1]
            yield status(), gr.skip()
            raise gr.Error(event[1], duration=None)
        elif kind == "done":
            current = f"100%  Done - {len(event[1]['clips'])} clips in {event[1]['output_folder']}"
            yield status(), event[1]["clips"]
            return


def build_ui() -> gr.Blocks:
    template_names, gallery_items, problem = startup()
    languages = [AUTO_LANGUAGE] + list(LANGUAGES.keys()) + [NO_CAPTIONS]

    with gr.Blocks(title="ClipForge") as demo:
        gr.Markdown("# ClipForge\nTurn one long video into short viral clips. "
                    "Pick a video, choose your options, press **Generate Clips**.")
        if problem:
            gr.Markdown(f"> **Setup problem:** {problem}")

        with gr.Row():
            with gr.Column(scale=1):
                with gr.Row():
                    path_box = gr.Textbox(label="YouTube link or video file", scale=5,
                                          placeholder="https://www.youtube.com/watch?v=...  or  /Users/you/Movies/video.mp4",
                                          info="Paste a YouTube link, or a local mp4, mkv, mov or webm "
                                               "(up to 10 hours). Local files are read in place. "
                                               "Only use videos you own or have permission to reuse.")
                    browse = gr.Button("Browse...", scale=1, min_width=110)
                with gr.Accordion("Or drop a small file here (it gets copied first)", open=False):
                    upload = gr.File(label="Video", file_types=list(SUPPORTED_EXTENSIONS),
                                     type="filepath")
                with gr.Row():
                    aspect = gr.Radio(list(ASPECT_RATIOS), value="9:16", label="Aspect ratio")
                    quality = gr.Radio(list(QUALITIES), value="1080p", label="Quality",
                                       info="Never goes above the source quality.")
                fit_mode = gr.Radio(list(FIT_MODES), value=FIT_MODES[0], label="Framing",
                                    info="Blurred background fit keeps the full frame.")
                length = gr.Radio(list(CLIP_LENGTHS) + [CUSTOM], value="60s", label="Clip length")
                with gr.Row(visible=False) as custom_row:
                    custom_min = gr.Number(value=20, label="Custom minimum (seconds)", precision=0)
                    custom_max = gr.Number(value=45, label="Custom maximum (seconds)", precision=0)
                n_clips = gr.Slider(1, 50, value=10, step=1, label="Number of clips")
                language = gr.Dropdown(languages, value=AUTO_LANGUAGE, label="Caption language",
                                       info="Pick a different language to translate the captions.")
            with gr.Column(scale=1):
                template = gr.Radio(template_names, label="Caption template",
                                    value=template_names[0] if template_names else None)
                gallery = gr.Gallery(value=gallery_items, label="Template previews (click to select)",
                                     columns=3, height=560, object_fit="contain",
                                     allow_preview=False, show_label=True)

        go = gr.Button("Generate Clips", variant="primary", size="lg")
        status_box = gr.Textbox(label="Progress", lines=10, max_lines=14, interactive=False)
        results = gr.State([])

        gr.Markdown("## Clips")

        @gr.render(inputs=results)
        def show_results(clips):
            if not clips:
                gr.Markdown("Finished clips will appear here.")
                return
            for clip in clips:
                with gr.Row(equal_height=False):
                    with gr.Column(scale=2, min_width=240):
                        gr.Video(value=clip["file"], label=f"Clip {clip['index']}", height=460,
                                 interactive=False)
                    with gr.Column(scale=3):
                        gr.Markdown(
                            f"### {clip['index']}. {clip['title']}\n"
                            f"**Virality score: {clip['score']}/100** - {clip['duration']:.0f}s "
                            f"(from {int(clip['start']) // 60}:{int(clip['start']) % 60:02d} "
                            f"in the source)\n\n"
                            + (f"**Hook:** {clip['hook']}\n\n" if clip["hook"] else "")
                            + (f"**Why:** {clip['reason']}" if clip["reason"] else ""))
                        gr.Textbox(value=clip["description"], label="Ready-to-paste description",
                                   lines=5, show_copy_button=True, interactive=False)
                        gr.DownloadButton("Download clip", value=clip["file"], variant="secondary")

        browse.click(lambda current: browse_for_video() or current, path_box, path_box)
        length.change(lambda choice: gr.update(visible=choice == CUSTOM), length, custom_row)

        def pick_template(evt: gr.SelectData):
            return gallery_items[evt.index][1]

        gallery.select(pick_template, None, template)
        go.click(generate,
                 [path_box, upload, aspect, quality, fit_mode, length, custom_min, custom_max,
                  n_clips, language, template],
                 [status_box, results], concurrency_limit=1)
    return demo


if __name__ == "__main__":
    build_ui().queue().launch(server_name="127.0.0.1", inbrowser=True,
                              allowed_paths=[str(OUTPUT_DIR), str(PREVIEW_DIR)])
