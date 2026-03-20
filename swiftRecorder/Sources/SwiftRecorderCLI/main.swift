import Foundation
import AVFoundation
import SwiftRecorder

/// CLI entry point for headless operation.
///
/// Usage:
///   swiftrecorder-cli devices             List cameras and audio devices
///   swiftrecorder-cli formats <index>     Show formats for a camera
///   swiftrecorder-cli record [options]    Record a clip
///
/// Record options:
///   --camera <index>      Video device index (default: 0)
///   --audio <index>       Audio device index (omit for no audio)
///   --width <px>          Video width (default: max)
///   --height <px>         Video height (default: max)
///   --fps <rate>          Frame rate (default: max)
///   --codec <codec>       h264, hevc, prores422, prores4444 (default: h264)
///   --container <type>    mov, mp4 (default: mov)
///   --duration <seconds>  Recording duration (default: 10)
///   --output <path>       Output directory (default: ~/Movies)
///   --name <base>         Base filename (default: recording)

// MARK: - Argument Parsing

let args = Array(CommandLine.arguments.dropFirst())

guard let command = args.first else {
    printUsage()
    exit(0)
}

DeviceEnumerator.enableDALDevices()

switch command {
case "devices":
    cmdDevices()

case "formats":
    let index = args.count > 1 ? Int(args[1]) ?? 0 : 0
    cmdFormats(index: index)

case "record":
    cmdRecord(args: Array(args.dropFirst()))

case "help", "--help", "-h":
    printUsage()

default:
    print("Unknown command: \(command)")
    printUsage()
    exit(1)
}

// MARK: - Commands

func printUsage() {
    print("""
    SwiftRecorder CLI — Native macOS camera recorder

    Usage:
      swiftrecorder-cli devices             List cameras and audio devices
      swiftrecorder-cli formats [index]      Show formats for camera (default: 0)
      swiftrecorder-cli record [options]     Record a clip

    Record options:
      --camera <index>       Video device index (default: 0)
      --audio <index>        Audio device index
      --width <px>           Width (default: max for camera)
      --height <px>          Height (default: max for camera)
      --fps <rate>           Frame rate (default: max for resolution)
      --codec <name>         h264, hevc, prores422, prores4444 (default: h264)
      --container <type>     mov, mp4 (default: mov)
      --duration <seconds>   Duration (default: 10)
      --output <dir>         Output directory (default: ~/Movies)
      --name <base>          Base filename (default: recording)
    """)
}

func cmdDevices() {
    DeviceEnumerator.printAllDevices()
}

func cmdFormats(index: Int) {
    let devices = DeviceEnumerator.videoDevices()
    guard index < devices.count else {
        print("Error: no video device at index \(index)")
        exit(1)
    }
    DeviceEnumerator.printFormats(for: devices[index].device)
}

func cmdRecord(args: [String]) {
    // Parse arguments
    var cameraIndex = 0
    var audioIndex: Int? = nil
    var width: Int? = nil
    var height: Int? = nil
    var fps: Double? = nil
    var codecName = "h264"
    var containerName = "mov"
    var duration: Double = 10
    var outputDir = FileManager.default.urls(for: .moviesDirectory, in: .userDomainMask).first!
    var baseName = "recording"

    var i = 0
    while i < args.count {
        switch args[i] {
        case "--camera":    i += 1; cameraIndex = Int(args[i]) ?? 0
        case "--audio":     i += 1; audioIndex = Int(args[i])
        case "--width":     i += 1; width = Int(args[i])
        case "--height":    i += 1; height = Int(args[i])
        case "--fps":       i += 1; fps = Double(args[i])
        case "--codec":     i += 1; codecName = args[i]
        case "--container": i += 1; containerName = args[i]
        case "--duration":  i += 1; duration = Double(args[i]) ?? 10
        case "--output":    i += 1; outputDir = URL(fileURLWithPath: args[i])
        case "--name":      i += 1; baseName = args[i]
        default: break
        }
        i += 1
    }

    // Resolve devices
    let videoDevices = DeviceEnumerator.videoDevices()
    guard cameraIndex < videoDevices.count else {
        print("Error: no video device at index \(cameraIndex)")
        exit(1)
    }
    let videoDevice = videoDevices[cameraIndex].device

    var audioDevice: AVCaptureDevice? = nil
    if let ai = audioIndex {
        let audioDevices = DeviceEnumerator.audioDevices()
        guard ai < audioDevices.count else {
            print("Error: no audio device at index \(ai)")
            exit(1)
        }
        audioDevice = audioDevices[ai].device
    }

    // Resolve defaults
    if width == nil || height == nil || fps == nil {
        let (dw, dh, df) = DeviceEnumerator.bestDefaults(for: videoDevice)
        width = width ?? dw
        height = height ?? dh
        fps = fps ?? df
    }

    let codec: RecordingEngine.VideoCodec = {
        switch codecName.lowercased() {
        case "h264", "avc": return .h264
        case "h265", "hevc": return .hevc
        case "prores422", "prores": return .prores422
        case "prores4444": return .prores4444
        default: return .h264
        }
    }()

    let container: RecordingEngine.Container = containerName.lowercased() == "mp4" ? .mp4 : .mov

    print("Recording \(duration)s from \(videoDevice.localizedName)")
    print("  \(width!)x\(height!)@\(String(format: "%.0f", fps!))fps \(codec.rawValue)/\(container.rawValue)")

    // Set up capture session
    let captureSession = CaptureSession()
    let engine = RecordingEngine(outputDirectory: outputDir, baseName: baseName)
    engine.codec = codec
    engine.container = container

    // Wire video/audio buffers to engine
    captureSession.addVideoHandler { buffer in
        engine.appendVideoSampleBuffer(buffer)
    }
    if audioDevice != nil {
        captureSession.addAudioHandler { buffer in
            engine.appendAudioSampleBuffer(buffer)
        }
    }

    do {
        try captureSession.configure(
            videoDevice: videoDevice,
            audioDevice: audioDevice,
            width: width,
            height: height,
            fps: fps
        )
    } catch {
        print("Error configuring capture: \(error)")
        exit(1)
    }

    captureSession.start()

    // Small delay to let the session warm up
    Thread.sleep(forTimeInterval: 0.5)

    do {
        let url = try engine.startRecording(
            width: captureSession.activeWidth,
            height: captureSession.activeHeight,
            fps: captureSession.activeFPS,
            hasAudio: audioDevice != nil
        )
        print("  Output: \(url.lastPathComponent)")
    } catch {
        print("Error starting recording: \(error)")
        exit(1)
    }

    // Wait for duration
    Thread.sleep(forTimeInterval: duration)

    // Stop
    let semaphore = DispatchSemaphore(value: 0)
    engine.stopRecording { url, dur in
        if let url = url {
            print("Done. Saved: \(url.lastPathComponent) (\(String(format: "%.1f", dur))s)")
        }
        semaphore.signal()
    }
    semaphore.wait()

    captureSession.stop()
    Thread.sleep(forTimeInterval: 0.5)
}
