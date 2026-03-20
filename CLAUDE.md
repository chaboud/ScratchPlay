# ScratchPlay

## Project Overview
This is a collection of utility tools. Contains `simpleRecorder` (Python) and `swiftRecorder` (native Swift/macOS).

## simpleRecorder
- **Location**: `simpleRecorder/`
- **Language**: Python 3.9+
- **Dependencies**: `opencv-python`, `numpy` (see `simpleRecorder/requirements.txt`)
- **Optional dependency**: `senxor` for Waveshare/Meridian MI48 thermal cameras
- **External requirement**: `ffmpeg` must be installed (`brew install ffmpeg`)
- **Platform**: macOS (uses AVFoundation via ffmpeg)

### Architecture
- `devices.py` — Device enumeration (cameras, audio) and format probing via ffmpeg/AVFoundation
- `recorder.py` — Core recording engine using ffmpeg subprocess (direct capture)
- `capture.py` — OpenCV-based frame grabber with background thread and circular pre-roll buffer
- `pipeline.py` — Unified pipeline: capture -> overlay -> preview -> ffmpeg pipe encode
- `overlay.py` — Timestamp and timecode overlay renderer (burned-in HH:MM:SS:FF)
- `thermal.py` — Thermal camera backends (InfiRay UVC + Waveshare/senxor), range lock, radiometric export
- `preview.py` — Live preview window (OpenCV highgui) for regular and thermal cameras
- `multicam.py` — Multi-camera simultaneous recording with composite preview grid
- `audiometer.py` — Real-time audio level meter (VU meter) via ffmpeg astats
- `cli.py` — CLI: devices, formats, record, interactive, preview, thermal, thermal-scan, multicam
- `gui.py` — Tkinter GUI with record/preview/thermal/multicam buttons

### Running
```bash
cd simpleRecorder
python cli.py devices                           # list cameras and audio
python cli.py formats -c 0                      # show camera formats
python cli.py record -t 30                      # one-shot (max res/fps default)
python cli.py interactive                       # spacebar start/stop clips
python cli.py preview -c 0 --pre-roll 10        # preview with 10s pre-roll buffer
python cli.py thermal -m infiray -c 2           # thermal preview
python cli.py thermal --range-min 20 --range-max 45  # locked range
python cli.py thermal-scan                      # detect thermal cameras
python cli.py multicam --cameras 0,1,2          # multi-cam recording
python gui.py                                   # launch GUI
```

### Key Features
- **Preview-while-recording**: See what you're capturing in real-time
- **Pre-roll buffer**: Configurable N-second buffer; recording includes footage before you pressed record
- **Timecode overlay**: Burned-in wall clock + recording timecode (HH:MM:SS:FF)
- **Multi-camera**: Simultaneous capture from multiple cameras with composite grid preview
- **Thermal range lock**: Lock colormap to specific temperature range for consistent visualization
- **Radiometric sidecar**: Per-frame CSV summary + binary float32 temperature arrays alongside thermal video
- **Audio level meter**: Real-time VU meter overlay with peak hold, green/yellow/red zones

### Thermal Camera Support
- **InfiRay** (TC001, TC004, P2 Pro): UVC devices with doubled frame (image + raw 16-bit temp data)
- **Waveshare/Meridian MI48** (80x62, 160x120): Serial-over-USB via `senxor` library

### Code Style
- No external linter configured; keep it simple and readable
- Use type hints where helpful but don't over-annotate
- Minimal dependencies — ffmpeg does the heavy lifting, OpenCV for preview/thermal

## swiftRecorder
- **Location**: `swiftRecorder/`
- **Language**: Swift 5.9+, SwiftUI, AVFoundation, VideoToolbox
- **Build**: `swift build` (SPM), no Xcode project needed
- **Platform**: macOS 13+ (Ventura)

### Architecture
- `Sources/SwiftRecorder/Camera/` — DeviceEnumerator, CaptureSession, AudioLevelMonitor
- `Sources/SwiftRecorder/Recording/` — RecordingEngine (AVAssetWriter + VideoToolbox)
- `Sources/SwiftRecorder/Views/` — SwiftUI views (preview, controls, VU meter)
- `Sources/SwiftRecorderApp/` — SwiftUI app entry point
- `Sources/SwiftRecorderCLI/` — Headless CLI for scripted recording

### Running
```bash
cd swiftRecorder
swift run swiftrecorder           # GUI app
swift run swiftrecorder-cli devices       # list devices
swift run swiftrecorder-cli record --duration 30  # headless recording
```

### Key Advantages over Python Version
- Hardware-accelerated H.264/HEVC/ProRes encoding via VideoToolbox
- GPU-composited preview (AVCaptureVideoPreviewLayer)
- Framework-level A/V sync
- Camera controls (exposure, focus, WB, ISO)
- Lower power consumption for long sessions
