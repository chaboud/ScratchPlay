# ScratchPlay

## Project Overview
This is a collection of utility tools. Currently contains `simpleRecorder`, a Python-based video recorder for macOS.

## simpleRecorder
- **Location**: `simpleRecorder/`
- **Language**: Python 3.9+
- **Dependencies**: `opencv-python`, `numpy` (see `simpleRecorder/requirements.txt`)
- **External requirement**: `ffmpeg` must be installed (`brew install ffmpeg`)
- **Platform**: macOS (uses AVFoundation via ffmpeg)

### Architecture
- `devices.py` — Device enumeration (cameras, audio) and format probing via ffmpeg/AVFoundation
- `recorder.py` — Core recording engine using ffmpeg subprocess
- `cli.py` — Command-line interface (one-shot recording + interactive spacebar mode)
- `gui.py` — Tkinter GUI with dropdown selectors and record button

### Running
```bash
cd simpleRecorder
python cli.py devices          # list cameras and audio devices
python cli.py formats -c 0     # show formats for camera 0
python cli.py record -t 30     # record 30 seconds
python cli.py interactive      # spacebar to start/stop clips
python gui.py                  # launch GUI
```

### Code Style
- No external linter configured; keep it simple and readable
- Use type hints where helpful but don't over-annotate
- Minimal dependencies — ffmpeg does the heavy lifting
