# ScratchPlay

A collection of video recording tools for macOS, implemented across multiple frameworks.

## Projects

| Project | Language | Description |
|---------|----------|-------------|
| [simpleRecorder](simpleRecorder/) | Python | CLI + GUI recorder using ffmpeg/AVFoundation. Thermal camera support. |
| [swiftRecorder](swiftRecorder/) | Swift | Native macOS recorder with hardware-accelerated encoding via VideoToolbox. |
| [flutterRecorder](flutterRecorder/) | Dart/Flutter | Cross-platform GUI that wraps ffmpeg for recording. |

## Quick Comparison

| Feature | simpleRecorder | swiftRecorder | flutterRecorder |
|---------|---------------|---------------|-----------------|
| Encoding | ffmpeg (CPU/VT) | VideoToolbox (GPU) | ffmpeg (CPU/VT) |
| Preview | OpenCV window | AVCaptureVideoPreviewLayer (GPU) | Launches Python preview |
| Thermal cameras | InfiRay + Waveshare | Not yet | Not yet |
| Multi-camera | Yes | Not yet | Launches Python multicam |
| Pre-roll buffer | Yes | Not yet | Via Python CLI |
| GUI options | Tkinter, PySide6, Dear PyGui, Textual | SwiftUI | Flutter/Material 3 |
| CLI | Full-featured | Basic | N/A |

## Requirements

- **macOS** (all projects use AVFoundation)
- **ffmpeg** — `brew install ffmpeg` (required by simpleRecorder and flutterRecorder)
- **Python 3.9–3.13** — for simpleRecorder
- **Swift 6.0+ / Xcode** — for swiftRecorder
- **Flutter SDK** — for flutterRecorder

## Getting Started

```bash
# Python recorder (fastest to get running)
cd simpleRecorder
pip install -r requirements.txt
python cli.py devices
python cli.py record -t 10

# Swift recorder (native performance)
cd swiftRecorder
swift run swiftrecorder

# Flutter recorder
cd flutterRecorder
flutter run -d macos
```

See each project's README for full documentation.
