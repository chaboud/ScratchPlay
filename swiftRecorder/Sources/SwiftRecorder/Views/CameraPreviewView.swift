import AVFoundation
import SwiftUI

/// NSViewRepresentable wrapping AVCaptureVideoPreviewLayer.
/// This is GPU-composited — no CPU frame copying for preview.
///
/// The preview layer is added as a sublayer (NOT as the root layer)
/// to avoid creating a "layer-hosting view" which interferes with
/// macOS focus/responder chain and breaks TextField editing.
public struct CameraPreviewView: NSViewRepresentable {
    let session: AVCaptureSession

    public init(session: AVCaptureSession) {
        self.session = session
    }

    public func makeNSView(context: Context) -> NSView {
        let view = PreviewNSView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspect
        return view
    }

    public func updateNSView(_ nsView: NSView, context: Context) {}
}

private class PreviewNSView: NSView {
    let previewLayer = AVCaptureVideoPreviewLayer()

    override init(frame: CGRect) {
        super.init(frame: frame)
        wantsLayer = true
        // Add as sublayer — do NOT replace root layer with `layer = previewLayer`.
        // Replacing the root layer creates a "layer-hosting view" that breaks
        // the window's focus/responder chain, making TextFields uneditable.
        layer?.addSublayer(previewLayer)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    // Prevent this view from ever stealing focus or intercepting events
    override var acceptsFirstResponder: Bool { false }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func becomeFirstResponder() -> Bool { false }

    override func layout() {
        super.layout()
        previewLayer.frame = bounds
    }
}
