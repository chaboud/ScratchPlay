# ScratchPlay

## Project Overview
This is a collection of utility tools. Currently contains `simpleRecorder`, a Python-based video recorder for macOS.

## simpleRecorder
- **Location**: `simpleRecorder/`
- **Language**: Python 3.9+
- **Dependencies**: `opencv-python`, `numpy` (see `simpleRecorder/requirements.txt`)
- **Optional dependency**: `senxor` for Waveshare/Meridian MI48 thermal cameras
- **External requirement**: `ffmpeg` must be installed (`brew install ffmpeg`)
- **Platform**: macOS (uses AVFoundation via ffmpeg)

### Architecture
- `devices.py` — Device enumeration (cameras, audio) and format probing via ffmpeg/AVFoundation
- `recorder.py` — Core recording engine using ffmpeg subprocess
- `thermal.py` — Thermal camera backends (InfiRay UVC + Waveshare/senxor) with colormap and temp overlay
- `preview.py` — Live preview window (OpenCV highgui) for regular and thermal cameras
- `cli.py` — Command-line interface (one-shot, interactive, preview, thermal, scan)
- `gui.py` — Tkinter GUI with dropdown selectors, record/preview/thermal buttons

### Running
```bash
cd simpleRecorder
python cli.py devices          # list cameras and audio devices
python cli.py formats -c 0     # show formats for camera 0
python cli.py record -t 30     # record 30 seconds (max res/fps by default)
python cli.py interactive      # spacebar to start/stop clips
python cli.py preview -c 0     # live preview window
python cli.py thermal -m infiray -c 2  # thermal camera preview
python cli.py thermal-scan     # detect thermal cameras
python gui.py                  # launch GUI
```

### Thermal Camera Support
- **InfiRay** (TC001, TC004, P2 Pro): UVC devices with doubled frame (image + raw 16-bit temp data)
- **Waveshare/Meridian MI48** (80x62, 160x120): Serial-over-USB via `senxor` library

### Code Style
- No external linter configured; keep it simple and readable
- Use type hints where helpful but don't over-annotate
- Minimal dependencies — ffmpeg does the heavy lifting, OpenCV for preview/thermal
