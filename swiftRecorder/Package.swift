// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SwiftRecorder",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "swiftrecorder", targets: ["SwiftRecorderApp"]),
        .executable(name: "swiftrecorder-cli", targets: ["SwiftRecorderCLI"]),
        .library(name: "SwiftRecorderLib", targets: ["SwiftRecorder"]),
    ],
    targets: [
        // Core library: camera, recording, views (no @main)
        .target(
            name: "SwiftRecorder",
            path: "Sources/SwiftRecorder"
        ),
        // SwiftUI app executable
        .executableTarget(
            name: "SwiftRecorderApp",
            dependencies: ["SwiftRecorder"],
            path: "Sources/SwiftRecorderApp"
        ),
        // CLI executable
        .executableTarget(
            name: "SwiftRecorderCLI",
            dependencies: ["SwiftRecorder"],
            path: "Sources/SwiftRecorderCLI"
        ),
    ]
)
