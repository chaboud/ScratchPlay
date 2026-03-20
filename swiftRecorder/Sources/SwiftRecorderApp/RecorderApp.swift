import SwiftUI
import AppKit
import SwiftRecorder

/// Entry point for the SwiftUI app.
/// Can be launched from the CLI target or embedded in an .app bundle.
@main
public struct RecorderApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    public init() {
        // When launched via `swift run`, the process isn't automatically
        // the frontmost app — keystrokes go to the terminal instead.
        DispatchQueue.main.async {
            NSApp.setActivationPolicy(.regular)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    public var body: some Scene {
        WindowGroup {
            RecorderView()
        }
        .windowStyle(.titleBar)
        .defaultSize(width: 900, height: 640)
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }
}
