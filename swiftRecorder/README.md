# swiftRecorder

Native macOS video recorder using AVFoundation, VideoToolbox, and SwiftUI.

## Why Native?

| Feature | Python (simpleRecorder) | Swift (swiftRecorder) |
|---------|------------------------|----------------------|
| H.264/HEVC encoding | ffmpeg (CPU or VideoToolbox via CLI) | VideoToolbox hardware encoder (zero-copy) |
| ProRes | ffmpeg software only | Native Apple Silicon media engine |
| Preview latency | OpenCV CPU frame copy | AVCaptureVideoPreviewLayer (GPU composited) |
| A/V sync | Separate capture processes | AVCaptureSession framework-level sync |
| Camera controls | None (ffmpeg passthrough) | Exposure, focus, WB, ISO via AVFoundation |
| Power efficiency | Python + OpenCV + ffmpeg overhead | Native process, minimal CPU usage |
| Thermal cameras | Full support (InfiRay + Waveshare) | Not yet (would need IOKit/USB) |

## Building

```bash
cd swiftRecorder

# Build everything
swift build

# Build release
swift build -c release

# Run the GUI app
swift run swiftrecorder

# Run the CLI
swift run swiftrecorder-cli devices
swift run swiftrecorder-cli formats 0
swift run swiftrecorder-cli record --duration 30
```

## Requirements

- macOS 15+ (Sequoia)
- Xcode Command Line Tools or Xcode 16+
- Camera and microphone permissions (granted on first run)

## Architecture

```
Sources/
├── SwiftRecorder/          # Core library
│   ├── Camera/
│   │   ├── DeviceEnumerator.swift   # Device discovery + format probing
│   │   ├── CaptureSession.swift     # AVCaptureSession management
│   │   └── AudioLevelMonitor.swift  # Real-time audio level metering
│   ├── Recording/
│   │   └── RecordingEngine.swift    # AVAssetWriter + VideoToolbox encoding
│   └── Views/
│       ├── RecorderView.swift       # Main SwiftUI interface
│       ├── RecorderViewModel.swift  # ViewModel binding capture + recording
│       ├── CameraPreviewView.swift  # GPU-composited preview layer
│       └── AudioMeterView.swift     # SwiftUI VU meter
├── SwiftRecorderApp/       # SwiftUI app entry point (@main)
│   └── RecorderApp.swift
└── SwiftRecorderCLI/       # Headless CLI
    └── main.swift
```

## CLI Usage

```bash
# List devices
swiftrecorder-cli devices

# Show camera formats
swiftrecorder-cli formats 0

# Record 4K HEVC for 60 seconds
swiftrecorder-cli record --width 3840 --height 2160 --fps 30 \
    --codec hevc --container mov --duration 60

# Record with audio
swiftrecorder-cli record --camera 0 --audio 0 --duration 30

# Record ProRes 422 (production quality)
swiftrecorder-cli record --codec prores422 --container mov --duration 10
```

## GUI Features

- Live preview via AVCaptureVideoPreviewLayer (GPU, no CPU copy)
- Camera and audio device selection dropdowns
- Resolution and FPS pickers (auto-populated from device, max selected by default)
- Codec selector: H.264, HEVC, ProRes 422, ProRes 4444
- Container selector: MOV, MP4
- Recording indicator with elapsed time and frame count
- Real-time audio level meter with peak hold
- Hardware-accelerated encoding via VideoToolbox

## Codecs

| Codec | Container | HW Accel | Use Case |
|-------|-----------|----------|----------|
| H.264 | MOV, MP4 | Yes | General purpose, maximum compatibility |
| HEVC  | MOV, MP4 | Yes | 40% smaller files, modern playback |
| ProRes 422 | MOV | Yes (M1 Pro+) | Post-production, editing |
| ProRes 4444 | MOV | Yes (M1 Pro+) | VFX, alpha channel, highest quality |
