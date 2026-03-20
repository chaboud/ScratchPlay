import AVFoundation
import Combine
import SwiftUI

/// ViewModel bridging the SwiftUI views with the capture session and recording engine.
public final class RecorderViewModel: ObservableObject {

    // MARK: - Published

    @Published var videoDevices: [AVCaptureDevice] = []
    @Published var audioDevices: [AVCaptureDevice] = []

    @Published var selectedVideoDevice: AVCaptureDevice? {
        didSet { onVideoDeviceChanged() }
    }
    @Published var selectedAudioDevice: AVCaptureDevice? {
        didSet { reconfigureSession() }
    }

    @Published var availableResolutions: [String] = []
    @Published var availableFPS: [String] = []

    @Published var selectedResolution: String = "" {
        didSet { onResolutionChanged() }
    }
    @Published var selectedFPS: String = "" {
        didSet { reconfigureSession() }
    }

    @Published var selectedCodec: RecordingEngine.VideoCodec = .h264
    @Published var selectedContainer: RecordingEngine.Container = .mov

    @Published var outputPath: String = FileManager.default.urls(for: .moviesDirectory, in: .userDomainMask).first!.path
    @Published var baseName: String = "recording"

    var outputDirectory: URL {
        URL(fileURLWithPath: outputPath)
    }

    // MARK: - Objects

    let captureSession = CaptureSession()
    let recordingEngine = RecordingEngine()
    let audioMonitor = AudioLevelMonitor()

    private var cancellables = Set<AnyCancellable>()

    // MARK: - Computed

    var formattedDuration: String {
        let t = recordingEngine.recordingDuration
        let hrs = Int(t) / 3600
        let mins = (Int(t) % 3600) / 60
        let secs = Int(t) % 60
        return String(format: "%02d:%02d:%02d", hrs, mins, secs)
    }

    // MARK: - Setup

    func setup() {
        DeviceEnumerator.enableDALDevices()
        refreshDevices()

        // Wire up sample buffer handlers
        captureSession.addVideoHandler { [weak self] buffer in
            self?.recordingEngine.appendVideoSampleBuffer(buffer)
        }
        captureSession.addAudioHandler { [weak self] buffer in
            self?.recordingEngine.appendAudioSampleBuffer(buffer)
            self?.audioMonitor.processSampleBuffer(buffer)
        }

        // Forward published state changes
        recordingEngine.objectWillChange.sink { [weak self] in
            self?.objectWillChange.send()
        }.store(in: &cancellables)

        captureSession.objectWillChange.sink { [weak self] in
            self?.objectWillChange.send()
        }.store(in: &cancellables)

        audioMonitor.objectWillChange.sink { [weak self] in
            self?.objectWillChange.send()
        }.store(in: &cancellables)
    }

    func refreshDevices() {
        videoDevices = DeviceEnumerator.videoDevices().map(\.device)
        audioDevices = DeviceEnumerator.audioDevices().map(\.device)

        if selectedVideoDevice == nil, let first = videoDevices.first {
            selectedVideoDevice = first
        }
    }

    // MARK: - Device / Format Changes

    private func onVideoDeviceChanged() {
        guard let device = selectedVideoDevice else { return }

        let resolutions = DeviceEnumerator.uniqueResolutions(for: device)
        availableResolutions = resolutions.map { "\($0.width)x\($0.height)" }

        // Default to max resolution
        if let first = availableResolutions.first {
            selectedResolution = first
        }
    }

    private func onResolutionChanged() {
        guard let device = selectedVideoDevice else { return }
        let parts = selectedResolution.split(separator: "x")
        guard parts.count == 2,
              let w = Int(parts[0]), let h = Int(parts[1]) else { return }

        // Get available FPS for this resolution
        let formats = DeviceEnumerator.formats(for: device)
            .filter { $0.width == w && $0.height == h }
        let allFPS = Set(formats.flatMap { $0.frameRates }).sorted()

        availableFPS = allFPS.map { String(format: "%.0f", $0) }

        // Default to max FPS
        if let last = availableFPS.last {
            selectedFPS = last
        }
    }

    private func reconfigureSession() {
        guard let device = selectedVideoDevice else { return }

        let parts = selectedResolution.split(separator: "x")
        let w = parts.count == 2 ? Int(parts[0]) : nil
        let h = parts.count == 2 ? Int(parts[1]) : nil
        let fps = Double(selectedFPS)

        do {
            try captureSession.configure(
                videoDevice: device,
                audioDevice: selectedAudioDevice,
                width: w,
                height: h,
                fps: fps
            )
            if !captureSession.isRunning {
                captureSession.start()
            }
        } catch {
            print("Failed to configure session: \(error)")
        }
    }

    // MARK: - Recording

    func toggleRecording() {
        if recordingEngine.isRecording {
            recordingEngine.stopRecording()
        } else {
            recordingEngine.codec = selectedCodec
            recordingEngine.container = selectedContainer
            recordingEngine.outputDirectory = outputDirectory
            recordingEngine.baseName = baseName

            do {
                let _ = try recordingEngine.startRecording(
                    width: captureSession.activeWidth,
                    height: captureSession.activeHeight,
                    fps: captureSession.activeFPS,
                    hasAudio: selectedAudioDevice != nil
                )
            } catch {
                print("Failed to start recording: \(error)")
            }
        }
    }
}
