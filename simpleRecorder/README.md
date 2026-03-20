# simpleRecorder

A Python video recorder for macOS that captures from connected cameras using ffmpeg and AVFoundation.

## Features

- Enumerate connected cameras and audio devices
- Query supported resolutions and frame rates per camera
- H.264 (AVC) and H.265 (HEVC) encoding
- MOV and MP4 containers
- Configurable quality (CRF), resolution, and frame rate
- Audio recording from selectable input device
- Preview-while-recording with live OpenCV window
- Pre-roll buffer — recording includes footage from before you pressed record
- Timecode overlay (wall clock + recording timecode, HH:MM:SS:FF)
- Audio level meter (VU meter with peak hold)
- Independent overlay controls for preview vs recorded file
- Multi-camera simultaneous recording with composite grid preview
- Thermal camera support (InfiRay UVC + Waveshare/senxor)
- Radiometric sidecar export for thermal cameras
- Multiple GUI options: Tkinter, PySide6, Dear PyGui, Textual TUI

## Requirements

- **macOS** (uses AVFoundation)
- **Python 3.9–3.13** (3.14 may have tkinter issues on Homebrew; CLI still works)
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

# Record 30 seconds (auto-detects best resolution/fps)
python cli.py record --duration 30

# Record to a specific path
python cli.py record -t 30 --target ~/Movies/myclip.mov

# Interactive mode (spacebar = start/stop, q = quit)
python cli.py interactive -c 0 --audio 0

# Interactive with live preview window
python cli.py interactive --preview --pre-roll 10

# Preview mode (R = record, T = toggle timecode, M = toggle meters)
python cli.py preview -c 0 --pre-roll 10

# Burn overlays into the recorded file
python cli.py preview --record-timecode --record-meters

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
| `--width, -W` | auto | Video width (default: max) |
| `--height, -H` | auto | Video height (default: max) |
| `--fps, -f` | auto | Frame rate (default: max) |
| `--codec` | h264 | h264, avc, h265, hevc |
| `--container` | mov | mov, mp4 |
| `--crf` | 20 | Quality (lower = better) |
| `--output-dir, -o` | . | Output directory |
| `--base-name, -n` | recording | Filename prefix |
| `--target, -T` | none | Exact output path (overrides -o, -n, container) |
| `--duration, -t` | 10 | Duration in seconds |

### `python cli.py interactive [options]`
Same options as `record` plus:

| Flag | Default | Description |
|------|---------|-------------|
| `--max-length` | none | Auto-stop clip after N seconds |
| `--preview` | off | Open live preview window (R key to record) |
| `--pre-roll` | 5 | Pre-roll buffer seconds (with `--preview`) |

### `python cli.py preview [options]`
Live preview window with recording capability. Same options as `record` plus pre-roll and overlay controls.

**Preview keys:** Q=quit, R=record, SPACE=snapshot, T=toggle timecode, M=toggle meters

### Overlay Controls

Overlays can be independently controlled for the preview window and the recorded file:

| Flag | Default | Description |
|------|---------|-------------|
| `--preview-timecode` / `--no-preview-timecode` | on | Timecode on preview |
| `--preview-meters` / `--no-preview-meters` | on | Audio meters on preview |
| `--record-timecode` | off | Burn timecode into recorded video |
| `--record-meters` | off | Burn audio meters into recorded video |

### `python cli.py thermal [options]`
Thermal camera preview with radiometric recording.

| Flag | Default | Description |
|------|---------|-------------|
| `--mode, -m` | infiray | infiray or waveshare |
| `--colormap` | inferno | Color palette for thermal visualization |
| `--range-min` / `--range-max` | auto | Lock temperature range (C) |

### `python cli.py multicam --cameras 0,1,2`
Multi-camera simultaneous recording with composite preview grid.

## GUI Options

All GUIs provide the same interface: device/format selectors, overlay controls, record/preview/thermal/multicam buttons.

| Command | Toolkit | Install |
|---------|---------|---------|
| `python gui.py` | Tkinter | `brew install python-tk@3.13` |
| `python gui_pyside.py` | PySide6 (Qt) | `pip install PySide6` |
| `python gui_dearpygui.py` | Dear PyGui | `pip install dearpygui` |
| `python gui_textual.py` | Textual (TUI) | `pip install textual` |

## Thermal Camera Support

- **InfiRay** (TC001, TC004, P2 Pro): UVC devices with doubled frame (image + raw 16-bit temp data)
- **Waveshare/Meridian MI48** (80x62, 160x120): Serial-over-USB via `senxor` library

### Thermal Keys
Q=quit, R=record, C=cycle colormap, SPACE=snapshot, L=lock range, U=unlock range

## Architecture

```
simpleRecorder/
├── cli.py            # CLI entry point
├── gui.py            # Tkinter GUI
├── gui_pyside.py     # PySide6 GUI
├── gui_dearpygui.py  # Dear PyGui GUI
├── gui_textual.py    # Textual TUI
├── devices.py        # Device enumeration and format probing
├── recorder.py       # Core ffmpeg recording engine
├── capture.py        # OpenCV frame grabber with pre-roll buffer
├── pipeline.py       # Unified pipeline: capture → overlay → preview → encode
├── overlay.py        # Timecode overlay renderer
├── preview.py        # OpenCV preview window (regular + thermal)
├── thermal.py        # Thermal camera backends
├── multicam.py       # Multi-camera recording
├── audiometer.py     # Audio level meter
└── requirements.txt  # Python dependencies
```
