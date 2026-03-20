import AVFoundation
import SwiftUI

/// NSViewRepresentable wrapping AVCaptureVideoPreviewLayer.
/// This is GPU-composited — no CPU frame copying for preview.
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
        layer = previewLayer
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override var acceptsFirstResponder: Bool { false }

    override func layout() {
        super.layout()
        previewLayer.frame = bounds
    }
}
