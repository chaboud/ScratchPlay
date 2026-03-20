# simpleRecorder

A simple Python video recorder for macOS that captures from connected cameras using ffmpeg and AVFoundation.

## Features

- Enumerate connected cameras and audio devices
- Query supported resolutions and frame rates per camera
- H.264 (AVC) and H.265 (HEVC) encoding
- MOV and MP4 containers
- Configurable quality (CRF), resolution, and frame rate
- Audio recording from selectable input device
- Three usage modes:
  - **One-shot CLI** — record a fixed-duration clip
  - **Interactive CLI** — spacebar to start/stop clips, runs indefinitely
  - **GUI** — tkinter window with dropdowns and a record button

## Requirements

- **macOS** (uses AVFoundation)
- **Python 3.9+**
- **ffmpeg** — install with `brew install ffmpeg`
- Python packages: `pip install -r requirements.txt`

## Quick Start

```bash
# Install ffmpeg
brew install ffmpeg

# Install Python deps
pip install -r requirements.txt

# List devices
python cli.py devices

# Show camera formats (resolutions, frame rates)
python cli.py formats --camera 0

# Record 30 seconds of 4K H.264
python cli.py record --camera 0 --width 3840 --height 2160 --fps 30 --duration 30

# Record 1080p HEVC in MOV
python cli.py record -c 0 -W 1920 -H 1080 -f 30 --codec hevc --container mov -t 60

# Interactive mode (spacebar = start/stop, q = quit)
python cli.py interactive -c 0 -W 1920 -H 1080 --audio 0

# Launch GUI
python gui.py
```

## CLI Reference

### `python cli.py devices`
Lists all available video and audio devices.

### `python cli.py formats --camera <index>`
Shows supported pixel formats, resolutions, and frame rates for a camera.

### `python cli.py record [options]`
Records a single clip.

| Flag | Default | Description |
|------|---------|-------------|
| `--camera, -c` | 0 | Video device index |
| `--audio, -a` | none | Audio device index |
| `--width, -W` | 1920 | Video width |
| `--height, -H` | 1080 | Video height |
| `--fps, -f` | 30 | Frame rate |
| `--codec` | h264 | h264, hevc |
| `--container` | mov | mov, mp4 |
| `--crf` | 20 | Quality (lower = better) |
| `--output-dir, -o` | . | Output directory |
| `--base-name, -n` | recording | Filename prefix |
| `--duration, -t` | 10 | Duration in seconds |

### `python cli.py interactive [options]`
Same options as `record` plus:

| Flag | Default | Description |
|------|---------|-------------|
| `--max-length` | none | Auto-stop clip after N seconds |

### `python gui.py`
Opens a tkinter window with:
- Camera and audio device dropdowns (with refresh)
- Resolution and FPS dropdowns (auto-populated from camera probe)
- Codec (H.264/HEVC) and container (MOV/MP4) selectors
- CRF quality spinner
- Output folder browser and base name field
- Record/Stop button with elapsed time display
