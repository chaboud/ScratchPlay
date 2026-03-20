import SwiftUI

/// Entry point for the SwiftUI app.
/// Can be launched from the CLI target or embedded in an .app bundle.
@main
public struct RecorderApp: App {
    public init() {}

    public var body: some Scene {
        WindowGroup {
            RecorderView()
        }
        .windowStyle(.titleBar)
        .defaultSize(width: 900, height: 640)
    }
}
