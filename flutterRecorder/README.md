# flutterRecorder

A Flutter/Dart macOS desktop app for video recording, using ffmpeg and AVFoundation under the hood.

## Features

- Device enumeration (cameras and audio) via ffmpeg
- Format probing with resolution and FPS selection
- H.264 and HEVC encoding via ffmpeg subprocess
- MOV and MP4 containers
- Configurable quality (CRF), pre-roll, and output path
- Record/Preview/Thermal/MultiCam buttons
- Thermal camera options (backend, colormap, range lock)
- Material 3 design with color-coded action buttons
- Status bar with recording indicator and elapsed time

## Requirements

- **macOS**
- **Flutter SDK** (with macOS desktop support enabled)
- **ffmpeg** — `brew install ffmpeg`
- **Python 3.9–3.13** + simpleRecorder deps (for Preview/Thermal/MultiCam, which launch `python cli.py`)

## Getting Started

```bash
cd flutterRecorder

# Run in debug mode
flutter run -d macos

# Build release
flutter build macos
```

## How It Works

- **Device enumeration**: Runs `ffmpeg -f avfoundation -list_devices true -i ""` and parses stderr
- **Format probing**: Runs `ffmpeg -f avfoundation -list_formats all -i "<device>"` and parses resolutions/FPS
- **Recording**: Spawns ffmpeg as a subprocess with the selected codec, resolution, FPS, and CRF. Sends `q` to stdin for graceful stop.
- **Preview/Thermal/MultiCam**: Launches `python3 cli.py preview|thermal|multicam` from the sibling `simpleRecorder/` directory, passing through all relevant arguments.
- **Folder browser**: Uses `osascript` to open a native macOS folder picker dialog.

## Architecture

```
flutterRecorder/
├── lib/
│   └── main.dart          # Complete app (~580 lines)
├── macos/                 # macOS runner (Xcode project, entitlements)
├── pubspec.yaml           # Dependencies (file_picker, path)
└── analysis_options.yaml  # Lint config
```

## Entitlements

The app has these macOS entitlements enabled:
- App sandbox disabled (needed for subprocess spawning)
- Camera access
- Microphone access
- File read/write access

## Dependencies

| Package | Purpose |
|---------|---------|
| `file_picker` | Native file/folder picker dialogs |
| `path` | Cross-platform path manipulation |
