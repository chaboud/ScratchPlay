import AVFoundation
import Combine
import VideoToolbox

/// Records video and audio using AVAssetWriter with hardware-accelerated encoding.
///
/// Supports:
/// - H.264 (AVC) via VideoToolbox hardware encoder
/// - H.265 (HEVC) via VideoToolbox hardware encoder
/// - ProRes 422 and ProRes 4444
/// - MOV and MP4 containers
/// - Simultaneous preview (zero-copy — just passes sample buffers through)
public final class RecordingEngine: ObservableObject {

    // MARK: - Configuration

    public enum VideoCodec: String, CaseIterable, Identifiable {
        case h264 = "H.264"
        case hevc = "HEVC"
        case prores422 = "ProRes 422"
        case prores4444 = "ProRes 4444"

        public var id: String { rawValue }

        var avCodecType: AVVideoCodecType {
            switch self {
            case .h264: return .h264
            case .hevc: return .hevc
            case .prores422: return .proRes422
            case .prores4444: return .proRes4444
            }
        }

        /// Whether this codec supports hardware acceleration on Apple Silicon.
        var isHardwareAccelerated: Bool {
            switch self {
            case .h264, .hevc: return true
            case .prores422, .prores4444: return true // M1 Pro+ has ProRes engine
            }
        }
    }

    public enum Container: String, CaseIterable, Identifiable {
        case mov = "MOV"
        case mp4 = "MP4"

        public var id: String { rawValue }

        var fileType: AVFileType {
            switch self {
            case .mov: return .mov
            case .mp4: return .mp4
            }
        }

        var ext: String {
            switch self {
            case .mov: return "mov"
            case .mp4: return "mp4"
            }
        }
    }

    // MARK: - Published State

    @Published public var isRecording = false
    @Published public var recordingDuration: TimeInterval = 0
    @Published public var currentOutputURL: URL?
    @Published public var framesWritten: Int = 0

    // MARK: - Properties

    public var codec: VideoCodec = .h264
    public var container: Container = .mov
    public var videoBitRate: Int? = nil  // nil = let the system decide
    public var audioBitRate: Int = 192_000
    public var outputDirectory: URL
    public var baseName: String = "recording"

    // MARK: - Internal

    private var assetWriter: AVAssetWriter?
    private var videoWriterInput: AVAssetWriterInput?
    private var audioWriterInput: AVAssetWriterInput?
    private var startTime: CMTime?
    private var sessionStartDate: Date?
    private var timer: Timer?

    private let writerQueue = DispatchQueue(label: "com.swiftrecorder.writer")

    // MARK: - Init

    public init(outputDirectory: URL? = nil, baseName: String = "recording") {
        self.outputDirectory = outputDirectory ?? FileManager.default.urls(
            for: .moviesDirectory, in: .userDomainMask
        ).first!
        self.baseName = baseName
    }

    // MARK: - Start / Stop

    /// Start recording. Call this before sending sample buffers.
    public func startRecording(width: Int, height: Int, fps: Double,
                                hasAudio: Bool = false) throws -> URL {
        let timestamp = Self.timestampString()
        let filename = "\(baseName)_\(timestamp).\(container.ext)"
        let url = outputDirectory.appendingPathComponent(filename)

        // Create output directory if needed
        try FileManager.default.createDirectory(at: outputDirectory,
                                                 withIntermediateDirectories: true)

        // Asset writer
        let writer = try AVAssetWriter(outputURL: url, fileType: container.fileType)

        // Video input
        var videoSettings: [String: Any] = [
            AVVideoCodecKey: codec.avCodecType,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
        ]

        // Compression settings for H.264/HEVC
        if codec == .h264 || codec == .hevc {
            var compression: [String: Any] = [
                AVVideoAverageBitRateKey: videoBitRate ?? Self.defaultBitRate(
                    width: width, height: height, fps: fps, codec: codec
                ),
                AVVideoExpectedSourceFrameRateKey: fps,
                AVVideoMaxKeyFrameIntervalKey: Int(fps * 2),  // keyframe every 2s
            ]
            // Request hardware acceleration
            compression[AVVideoProfileLevelKey] = codec == .h264
                ? AVVideoProfileLevelH264HighAutoLevel
                : nil  // HEVC auto-selects profile
            videoSettings[AVVideoCompressionPropertiesKey] = compression
        }

        let videoInput = AVAssetWriterInput(
            mediaType: .video,
            outputSettings: videoSettings
        )
        videoInput.expectsMediaDataInRealTime = true

        if writer.canAdd(videoInput) {
            writer.add(videoInput)
        }

        // Audio input
        var audioInput: AVAssetWriterInput?
        if hasAudio {
            let audioSettings: [String: Any] = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 44100,
                AVNumberOfChannelsKey: 2,
                AVEncoderBitRateKey: audioBitRate,
            ]
            let ai = AVAssetWriterInput(mediaType: .audio, outputSettings: audioSettings)
            ai.expectsMediaDataInRealTime = true
            if writer.canAdd(ai) {
                writer.add(ai)
            }
            audioInput = ai
        }

        guard writer.startWriting() else {
            throw RecorderError.cannotCreateAssetWriter(
                writer.error?.localizedDescription ?? "unknown error"
            )
        }

        self.assetWriter = writer
        self.videoWriterInput = videoInput
        self.audioWriterInput = audioInput
        self.startTime = nil
        self.framesWritten = 0
        self.currentOutputURL = url
        self.sessionStartDate = Date()

        DispatchQueue.main.async {
            self.isRecording = true
            self.recordingDuration = 0
            self._startTimer()
        }

        print("Recording started: \(filename) [\(codec.rawValue)/\(container.rawValue)]")
        print("  \(width)x\(height)@\(String(format: "%.0f", fps))fps")

        return url
    }

    /// Stop recording and finalize the file.
    public func stopRecording(completion: ((URL?, TimeInterval) -> Void)? = nil) {
        writerQueue.async { [weak self] in
            guard let self = self, let writer = self.assetWriter else {
                completion?(nil, 0)
                return
            }

            let duration = self.recordingDuration
            let url = self.currentOutputURL

            self.videoWriterInput?.markAsFinished()
            self.audioWriterInput?.markAsFinished()

            writer.finishWriting {
                DispatchQueue.main.async {
                    self.isRecording = false
                    self._stopTimer()
                    self.assetWriter = nil
                    self.videoWriterInput = nil
                    self.audioWriterInput = nil
                    self.startTime = nil

                    if let url = url {
                        print("Recording stopped: \(url.lastPathComponent) (\(String(format: "%.1f", duration))s)")
                    }
                    completion?(url, duration)
                }
            }
        }
    }

    // MARK: - Sample Buffer Processing

    /// Call this for each video sample buffer from the capture session.
    public func appendVideoSampleBuffer(_ sampleBuffer: CMSampleBuffer) {
        writerQueue.async { [weak self] in
            guard let self = self,
                  let writer = self.assetWriter,
                  let input = self.videoWriterInput,
                  writer.status == .writing,
                  input.isReadyForMoreMediaData else { return }

            let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)

            if self.startTime == nil {
                self.startTime = pts
                writer.startSession(atSourceTime: pts)
            }

            input.append(sampleBuffer)
            self.framesWritten += 1
        }
    }

    /// Call this for each audio sample buffer from the capture session.
    public func appendAudioSampleBuffer(_ sampleBuffer: CMSampleBuffer) {
        writerQueue.async { [weak self] in
            guard let self = self,
                  let input = self.audioWriterInput,
                  self.assetWriter?.status == .writing,
                  self.startTime != nil,  // only after video has started
                  input.isReadyForMoreMediaData else { return }

            input.append(sampleBuffer)
        }
    }

    // MARK: - Timer

    private func _startTimer() {
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self = self, let start = self.sessionStartDate else { return }
            self.recordingDuration = Date().timeIntervalSince(start)
        }
    }

    private func _stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    // MARK: - Helpers

    /// Sensible default bit rate based on resolution and codec.
    static func defaultBitRate(width: Int, height: Int, fps: Double, codec: VideoCodec) -> Int {
        let pixels = width * height
        let base: Double
        switch codec {
        case .h264:
            // ~8 Mbps for 1080p30, scale with pixels and fps
            base = 8_000_000
        case .hevc:
            // HEVC is ~40% more efficient
            base = 5_000_000
        case .prores422, .prores4444:
            // ProRes doesn't use bitrate control the same way
            base = 100_000_000
        }
        let scale = (Double(pixels) / (1920.0 * 1080.0)) * (fps / 30.0)
        return Int(base * scale)
    }

    static func timestampString() -> String {
        let df = DateFormatter()
        df.dateFormat = "yyyyMMdd_HHmmss"
        return df.string(from: Date())
    }
}
