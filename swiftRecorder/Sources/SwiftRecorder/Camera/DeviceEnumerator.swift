import AVFoundation
import CoreMediaIO

/// Enumerates available video and audio capture devices and their capabilities.
public final class DeviceEnumerator {

    /// A supported format for a camera device.
    public struct CameraFormat: CustomStringConvertible {
        public let width: Int
        public let height: Int
        public let frameRates: [Double]   // min..max available
        public let mediaSubType: CMFormatDescription.MediaSubType
        public let formatDescription: CMFormatDescription

        public var description: String {
            let fpsStr = frameRates.map { String(format: "%.0f", $0) }.joined(separator: "/")
            return "\(width)x\(height)@\(fpsStr)fps [\(Self.label(for: mediaSubType))]"
        }

        public var pixelCount: Int { width * height }

        /// Human-readable label for a media subtype FourCC.
        public static func label(for subType: CMFormatDescription.MediaSubType) -> String {
            switch subType {
            case .init(rawValue: kCMVideoCodecType_JPEG),
                 .init(rawValue: kCMVideoCodecType_JPEG_OpenDML):
                return "MJPEG"
            case .init(rawValue: kCMVideoCodecType_H264):
                return "H.264"
            case .init(rawValue: kCMVideoCodecType_HEVC):
                return "HEVC"
            default:
                // Decode FourCC to readable string (e.g. "420v", "yuvs", "2vuy")
                let raw = subType.rawValue
                let c1 = Character(UnicodeScalar((raw >> 24) & 0xFF)!)
                let c2 = Character(UnicodeScalar((raw >> 16) & 0xFF)!)
                let c3 = Character(UnicodeScalar((raw >> 8) & 0xFF)!)
                let c4 = Character(UnicodeScalar(raw & 0xFF)!)
                let fourCC = String([c1, c2, c3, c4])
                    .trimmingCharacters(in: .whitespaces)
                return fourCC.isEmpty ? "raw" : fourCC
            }
        }
    }

    /// Info about a discovered device.
    public struct DeviceInfo: Identifiable {
        public let id: String
        public let name: String
        public let device: AVCaptureDevice
        public let isVideo: Bool
        public let isAudio: Bool
    }

    // MARK: - Device Discovery

    /// Enable access to DAL plugins (screen capture, USB cameras that need it).
    public static func enableDALDevices() {
        var prop = CMIOObjectPropertyAddress(
            mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
            mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
            mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain)
        )
        var allow: UInt32 = 1
        CMIOObjectSetPropertyData(
            CMIOObjectID(kCMIOObjectSystemObject),
            &prop, 0, nil,
            UInt32(MemoryLayout<UInt32>.size), &allow
        )
    }

    /// All video capture devices.
    public static func videoDevices() -> [DeviceInfo] {
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.builtInWideAngleCamera, .external],
            mediaType: .video,
            position: .unspecified
        )
        return discovery.devices.map { dev in
            DeviceInfo(
                id: dev.uniqueID,
                name: dev.localizedName,
                device: dev,
                isVideo: true,
                isAudio: false
            )
        }
    }

    /// All audio capture devices.
    public static func audioDevices() -> [DeviceInfo] {
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.builtInMicrophone, .external],
            mediaType: .audio,
            position: .unspecified
        )
        return discovery.devices.map { dev in
            DeviceInfo(
                id: dev.uniqueID,
                name: dev.localizedName,
                device: dev,
                isVideo: false,
                isAudio: true
            )
        }
    }

    // MARK: - Format Probing

    /// Get all supported formats for a video device, sorted by resolution descending.
    public static func formats(for device: AVCaptureDevice) -> [CameraFormat] {
        var results: [CameraFormat] = []

        for format in device.formats {
            let desc = format.formatDescription
            let dims = CMVideoFormatDescriptionGetDimensions(desc)
            let subType = CMFormatDescriptionGetMediaSubType(desc)

            var frameRates: [Double] = []
            for range in format.videoSupportedFrameRateRanges {
                // Collect both min and max; they're often the same or a range
                frameRates.append(range.maxFrameRate)
                if range.minFrameRate != range.maxFrameRate {
                    frameRates.append(range.minFrameRate)
                }
            }
            frameRates = Array(Set(frameRates)).sorted()

            let camFmt = CameraFormat(
                width: Int(dims.width),
                height: Int(dims.height),
                frameRates: frameRates,
                mediaSubType: CMFormatDescription.MediaSubType(rawValue: subType),
                formatDescription: desc
            )
            results.append(camFmt)
        }

        // Sort: highest resolution first, then highest fps
        results.sort { a, b in
            if a.pixelCount != b.pixelCount { return a.pixelCount > b.pixelCount }
            return (a.frameRates.last ?? 0) > (b.frameRates.last ?? 0)
        }

        return results
    }

    /// Unique resolutions available, sorted by pixel count descending.
    public static func uniqueResolutions(for device: AVCaptureDevice) -> [(width: Int, height: Int)] {
        var seen = Set<String>()
        var result: [(Int, Int)] = []
        for fmt in formats(for: device) {
            let key = "\(fmt.width)x\(fmt.height)"
            if seen.insert(key).inserted {
                result.append((fmt.width, fmt.height))
            }
        }
        return result
    }

    /// Unique camera modes (media subtypes) for a given resolution, e.g. ["MJPEG", "420v"].
    public static func uniqueModes(for device: AVCaptureDevice, width: Int, height: Int) -> [(label: String, subType: CMFormatDescription.MediaSubType)] {
        var seen = Set<FourCharCode>()
        var result: [(String, CMFormatDescription.MediaSubType)] = []
        for fmt in formats(for: device).filter({ $0.width == width && $0.height == height }) {
            let raw = fmt.mediaSubType.rawValue
            if seen.insert(raw).inserted {
                result.append((CameraFormat.label(for: fmt.mediaSubType), fmt.mediaSubType))
            }
        }
        return result
    }

    /// Max frame rate available for a given resolution and optional media subtype.
    public static func maxFrameRate(for device: AVCaptureDevice, width: Int, height: Int,
                                     mediaSubType: CMFormatDescription.MediaSubType? = nil) -> Double {
        let matching = formats(for: device).filter {
            $0.width == width && $0.height == height
            && (mediaSubType == nil || $0.mediaSubType == mediaSubType)
        }
        return matching.compactMap { $0.frameRates.last }.max() ?? 30
    }

    /// Find the best format matching target resolution, frame rate, and optional media subtype.
    /// Returns the AVCaptureDevice.Format and the exact frame rate range to use.
    public static func bestFormat(
        for device: AVCaptureDevice,
        width: Int, height: Int, fps: Double,
        mediaSubType: CMFormatDescription.MediaSubType? = nil
    ) -> (AVCaptureDevice.Format, AVFrameRateRange)? {
        for format in device.formats {
            let desc = format.formatDescription
            let dims = CMVideoFormatDescriptionGetDimensions(desc)
            guard Int(dims.width) == width, Int(dims.height) == height else { continue }

            if let wanted = mediaSubType {
                let actual = CMFormatDescription.MediaSubType(rawValue: CMFormatDescriptionGetMediaSubType(desc))
                guard actual == wanted else { continue }
            }

            for range in format.videoSupportedFrameRateRanges {
                if range.minFrameRate <= fps && fps <= range.maxFrameRate {
                    return (format, range)
                }
            }
        }
        return nil
    }

    /// Get the maximum resolution and frame rate for a device.
    public static func bestDefaults(for device: AVCaptureDevice) -> (width: Int, height: Int, fps: Double) {
        let fmts = formats(for: device)
        guard let best = fmts.first else { return (1920, 1080, 30) }
        let fps = best.frameRates.last ?? 30
        return (best.width, best.height, fps)
    }

    // MARK: - Print Helpers

    public static func printAllDevices() {
        enableDALDevices()

        let videos = videoDevices()
        print("=== Video Devices ===")
        if videos.isEmpty { print("  (none found)") }
        for (i, dev) in videos.enumerated() {
            print("  [\(i)] \(dev.name) (\(dev.id))")
        }

        let audios = audioDevices()
        print("\n=== Audio Devices ===")
        if audios.isEmpty { print("  (none found)") }
        for (i, dev) in audios.enumerated() {
            print("  [\(i)] \(dev.name) (\(dev.id))")
        }
    }

    public static func printFormats(for device: AVCaptureDevice) {
        let fmts = formats(for: device)
        print("=== Formats for \(device.localizedName) ===")
        let resolutions = uniqueResolutions(for: device)
        for (w, h) in resolutions {
            let modes = uniqueModes(for: device, width: w, height: h)
            for (label, subType) in modes {
                let matching = fmts.filter { $0.width == w && $0.height == h && $0.mediaSubType == subType }
                let allFPS = Set(matching.flatMap { $0.frameRates }).sorted()
                let fpsStr = allFPS.map { String(format: "%.0f", $0) }.joined(separator: ", ")
                print("  \(w)x\(h) [\(label)] @ \(fpsStr) fps")
            }
        }
    }
}
