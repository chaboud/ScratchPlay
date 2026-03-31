import AVFoundation
import Combine

/// Manages an AVCaptureSession with video and optional audio inputs,
/// providing a publisher for sample buffers (for preview and recording).
public final class CaptureSession: NSObject, ObservableObject {

    // MARK: - Published State

    @Published public var isRunning = false
    @Published public var currentVideoDevice: AVCaptureDevice?
    @Published public var currentAudioDevice: AVCaptureDevice?
    @Published public var activeWidth: Int = 0
    @Published public var activeHeight: Int = 0
    @Published public var activeFPS: Double = 0

    // MARK: - Session

    public let session = AVCaptureSession()

    private var videoInput: AVCaptureDeviceInput?
    private var audioInput: AVCaptureDeviceInput?
    private let videoOutput = AVCaptureVideoDataOutput()
    private let audioOutput = AVCaptureAudioDataOutput()

    private let sessionQueue = DispatchQueue(label: "com.swiftrecorder.session")
    private let outputQueue = DispatchQueue(label: "com.swiftrecorder.output")

    /// Callbacks for video sample buffers (preview, recording, etc.)
    private var videoHandlers: [(CMSampleBuffer) -> Void] = []
    private var audioHandlers: [(CMSampleBuffer) -> Void] = []
    private let handlerLock = NSLock()

    // MARK: - Setup

    public func addVideoHandler(_ handler: @escaping (CMSampleBuffer) -> Void) {
        handlerLock.lock()
        videoHandlers.append(handler)
        handlerLock.unlock()
    }

    public func addAudioHandler(_ handler: @escaping (CMSampleBuffer) -> Void) {
        handlerLock.lock()
        audioHandlers.append(handler)
        handlerLock.unlock()
    }

    /// Configure the session with a video device and optional audio device.
    public func configure(
        videoDevice: AVCaptureDevice,
        audioDevice: AVCaptureDevice? = nil,
        width: Int? = nil,
        height: Int? = nil,
        fps: Double? = nil,
        mediaSubType: CMFormatDescription.MediaSubType? = nil
    ) throws {
        session.beginConfiguration()
        defer { session.commitConfiguration() }

        // Remove existing inputs
        if let vi = videoInput { session.removeInput(vi) }
        if let ai = audioInput { session.removeInput(ai) }

        // Video input
        let vInput = try AVCaptureDeviceInput(device: videoDevice)
        guard session.canAddInput(vInput) else {
            throw RecorderError.cannotAddInput(videoDevice.localizedName)
        }
        session.addInput(vInput)
        videoInput = vInput
        currentVideoDevice = videoDevice

        // Set format and frame rate
        let targetW = width ?? 0
        let targetH = height ?? 0
        let targetFPS = fps ?? 0

        if targetW > 0 && targetH > 0 {
            if let (format, fpsRange) = DeviceEnumerator.bestFormat(
                for: videoDevice, width: targetW, height: targetH,
                fps: targetFPS > 0 ? targetFPS : DeviceEnumerator.maxFrameRate(for: videoDevice, width: targetW, height: targetH, mediaSubType: mediaSubType),
                mediaSubType: mediaSubType
            ) {
                try videoDevice.lockForConfiguration()
                videoDevice.activeFormat = format
                let actualFPS = min(max(targetFPS, fpsRange.minFrameRate), fpsRange.maxFrameRate)
                videoDevice.activeVideoMinFrameDuration = CMTime(value: 1, timescale: CMTimeScale(actualFPS))
                videoDevice.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: CMTimeScale(actualFPS))
                videoDevice.unlockForConfiguration()
            }
        } else {
            // Use best defaults
            let (w, h, f) = DeviceEnumerator.bestDefaults(for: videoDevice)
            if let (format, _) = DeviceEnumerator.bestFormat(
                for: videoDevice, width: w, height: h, fps: f
            ) {
                try videoDevice.lockForConfiguration()
                videoDevice.activeFormat = format
                videoDevice.activeVideoMinFrameDuration = CMTime(value: 1, timescale: CMTimeScale(f))
                videoDevice.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: CMTimeScale(f))
                videoDevice.unlockForConfiguration()
            }
        }

        // Read back active dimensions
        let dims = CMVideoFormatDescriptionGetDimensions(videoDevice.activeFormat.formatDescription)
        activeWidth = Int(dims.width)
        activeHeight = Int(dims.height)
        activeFPS = 1.0 / CMTimeGetSeconds(videoDevice.activeVideoMinFrameDuration)

        // Video output
        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ]
        videoOutput.setSampleBufferDelegate(self, queue: outputQueue)
        if session.canAddOutput(videoOutput) {
            session.addOutput(videoOutput)
        }

        // Audio input (optional)
        if let audioDev = audioDevice {
            do {
                let aInput = try AVCaptureDeviceInput(device: audioDev)
                if session.canAddInput(aInput) {
                    session.addInput(aInput)
                    audioInput = aInput
                    currentAudioDevice = audioDev

                    audioOutput.setSampleBufferDelegate(self, queue: outputQueue)
                    if session.canAddOutput(audioOutput) {
                        session.addOutput(audioOutput)
                    }
                }
            } catch {
                print("Warning: could not add audio device: \(error)")
            }
        }
    }

    private func fpsRange(format: AVCaptureDevice.Format?) -> Double {
        guard let fmt = format else { return 30 }
        return fmt.videoSupportedFrameRateRanges.first?.maxFrameRate ?? 30
    }

    // MARK: - Start / Stop

    public func start() {
        sessionQueue.async { [weak self] in
            self?.session.startRunning()
            DispatchQueue.main.async {
                self?.isRunning = true
            }
        }
    }

    public func stop() {
        sessionQueue.async { [weak self] in
            self?.session.stopRunning()
            DispatchQueue.main.async {
                self?.isRunning = false
            }
        }
    }
}

// MARK: - AVCaptureVideoDataOutputSampleBufferDelegate

extension CaptureSession: AVCaptureVideoDataOutputSampleBufferDelegate, AVCaptureAudioDataOutputSampleBufferDelegate {
    public func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        handlerLock.lock()
        let vHandlers = videoHandlers
        let aHandlers = audioHandlers
        handlerLock.unlock()

        if output is AVCaptureVideoDataOutput {
            for handler in vHandlers {
                handler(sampleBuffer)
            }
        } else if output is AVCaptureAudioDataOutput {
            for handler in aHandlers {
                handler(sampleBuffer)
            }
        }
    }
}

// MARK: - Errors

public enum RecorderError: Error, LocalizedError {
    case cannotAddInput(String)
    case cannotCreateAssetWriter(String)
    case encodingFailed(String)

    public var errorDescription: String? {
        switch self {
        case .cannotAddInput(let name): return "Cannot add input: \(name)"
        case .cannotCreateAssetWriter(let msg): return "Asset writer error: \(msg)"
        case .encodingFailed(let msg): return "Encoding failed: \(msg)"
        }
    }
}
