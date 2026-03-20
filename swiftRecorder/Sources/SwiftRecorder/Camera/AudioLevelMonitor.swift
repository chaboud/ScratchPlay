import AVFoundation
import Combine

/// Monitors audio levels from an AVCaptureSession's audio output.
/// Provides real-time peak and average power levels.
public final class AudioLevelMonitor: ObservableObject {

    @Published public var peakLevel: Float = -60    // dBFS
    @Published public var averageLevel: Float = -60 // dBFS
    @Published public var peakHold: Float = -60     // peak hold with decay

    private var peakHoldTime: Date = .distantPast
    private let decayRate: Float = 0.15  // dB per update

    /// Process an audio sample buffer and update levels.
    public func processSampleBuffer(_ sampleBuffer: CMSampleBuffer) {
        guard let channelData = extractChannelData(sampleBuffer) else { return }

        var peak: Float = 0
        var sum: Float = 0
        let count = channelData.count

        for sample in channelData {
            let abs = Swift.abs(sample)
            if abs > peak { peak = abs }
            sum += abs * abs
        }

        let rms = count > 0 ? sqrt(sum / Float(count)) : 0

        let peakDB = peak > 0 ? 20 * log10(peak) : -60
        let rmsDB = rms > 0 ? 20 * log10(rms) : -60

        DispatchQueue.main.async {
            // Fast attack, slow decay
            if peakDB > self.peakLevel {
                self.peakLevel = peakDB
            } else {
                self.peakLevel += (peakDB - self.peakLevel) * 0.3
            }

            if rmsDB > self.averageLevel {
                self.averageLevel = rmsDB
            } else {
                self.averageLevel += (rmsDB - self.averageLevel) * 0.2
            }

            // Peak hold: 2 second hold then decay
            if self.peakLevel > self.peakHold {
                self.peakHold = self.peakLevel
                self.peakHoldTime = Date()
            } else if Date().timeIntervalSince(self.peakHoldTime) > 2.0 {
                self.peakHold -= self.decayRate
                if self.peakHold < self.peakLevel {
                    self.peakHold = self.peakLevel
                }
            }
        }
    }

    private func extractChannelData(_ sampleBuffer: CMSampleBuffer) -> [Float]? {
        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return nil }

        var lengthAtOffset: Int = 0
        var totalLength: Int = 0
        var dataPointer: UnsafeMutablePointer<Int8>?

        let status = CMBlockBufferGetDataPointer(
            blockBuffer, atOffset: 0, lengthAtOffsetOut: &lengthAtOffset,
            totalLengthOut: &totalLength, dataPointerOut: &dataPointer
        )
        guard status == kCMBlockBufferNoErr, let data = dataPointer else { return nil }

        // Assume 32-bit float PCM
        let floatCount = totalLength / MemoryLayout<Float>.size
        let floatPointer = UnsafeRawPointer(data).bindMemory(to: Float.self, capacity: floatCount)
        return Array(UnsafeBufferPointer(start: floatPointer, count: floatCount))
    }
}
