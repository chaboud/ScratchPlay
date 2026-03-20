import AppKit
import AVFoundation
import SwiftUI
import UniformTypeIdentifiers

/// Main SwiftUI view for the recorder app.
/// Provides device selectors, format controls, preview, and record button.
public struct RecorderView: View {
    @StateObject private var viewModel = RecorderViewModel()

    public init() {}

    public var body: some View {
        VStack(spacing: 0) {
            // Preview
            ZStack(alignment: .topLeading) {
                CameraPreviewView(session: viewModel.captureSession.session)
                    .frame(minWidth: 640, minHeight: 360)
                    .background(Color.black)

                // Recording indicator
                if viewModel.recordingEngine.isRecording {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(Color.red)
                            .frame(width: 10, height: 10)
                        Text("REC \(viewModel.formattedDuration)")
                            .font(.system(size: 14, weight: .bold, design: .monospaced))
                            .foregroundColor(.white)
                    }
                    .padding(8)
                    .background(Color.black.opacity(0.6))
                    .cornerRadius(6)
                    .padding(12)
                }
            }

            Divider()

            // Controls
            VStack(spacing: 12) {
                // Device selection
                HStack {
                    VStack(alignment: .leading) {
                        Text("Camera").font(.caption).foregroundColor(.secondary)
                        Picker("Camera", selection: $viewModel.selectedVideoDevice) {
                            Text("None").tag(nil as AVCaptureDevice?)
                            ForEach(viewModel.videoDevices, id: \.uniqueID) { dev in
                                Text(dev.localizedName).tag(dev as AVCaptureDevice?)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 220)
                    }

                    VStack(alignment: .leading) {
                        Text("Audio").font(.caption).foregroundColor(.secondary)
                        Picker("Audio", selection: $viewModel.selectedAudioDevice) {
                            Text("None").tag(nil as AVCaptureDevice?)
                            ForEach(viewModel.audioDevices, id: \.uniqueID) { dev in
                                Text(dev.localizedName).tag(dev as AVCaptureDevice?)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 220)
                    }

                    Spacer()

                    Button("Refresh") { viewModel.refreshDevices() }
                }

                // Format selection
                HStack {
                    VStack(alignment: .leading) {
                        Text("Resolution").font(.caption).foregroundColor(.secondary)
                        Picker("Resolution", selection: $viewModel.selectedResolution) {
                            ForEach(viewModel.availableResolutions, id: \.self) { res in
                                Text(res).tag(res)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 130)
                    }

                    VStack(alignment: .leading) {
                        Text("FPS").font(.caption).foregroundColor(.secondary)
                        Picker("FPS", selection: $viewModel.selectedFPS) {
                            ForEach(viewModel.availableFPS, id: \.self) { fps in
                                Text(fps).tag(fps)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 70)
                    }

                    VStack(alignment: .leading) {
                        Text("Codec").font(.caption).foregroundColor(.secondary)
                        Picker("Codec", selection: $viewModel.selectedCodec) {
                            ForEach(RecordingEngine.VideoCodec.allCases) { codec in
                                Text(codec.rawValue).tag(codec)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 110)
                    }

                    VStack(alignment: .leading) {
                        Text("Container").font(.caption).foregroundColor(.secondary)
                        Picker("Container", selection: $viewModel.selectedContainer) {
                            ForEach(RecordingEngine.Container.allCases) { c in
                                Text(c.rawValue).tag(c)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 70)
                    }

                    VStack(alignment: .leading) {
                        Text("Quality").font(.caption).foregroundColor(.secondary)
                        Picker("Quality", selection: $viewModel.selectedQuality) {
                            ForEach(RecorderViewModel.VideoQuality.allCases) { q in
                                Text(q.rawValue).tag(q)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 90)
                    }

                    if viewModel.selectedQuality == .custom {
                        VStack(alignment: .leading) {
                            Text("Bitrate (Mbps)").font(.caption).foregroundColor(.secondary)
                            TextField("e.g. 8.0", text: $viewModel.customBitrateMbps)
                                .frame(width: 70)
                                .textFieldStyle(.roundedBorder)
                        }
                    }

                    VStack(alignment: .leading) {
                        Text("").font(.caption)
                        Text(viewModel.effectiveBitrateMbps)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(.secondary)
                    }

                    Spacer()
                }

                // Output settings — single path field: /dir/basename
                HStack {
                    VStack(alignment: .leading) {
                        Text("Save to (folder / base name)").font(.caption).foregroundColor(.secondary)
                        HStack {
                            TextField("/path/to/basename", text: $viewModel.savePath)
                                .frame(minWidth: 350)
                                .textFieldStyle(.roundedBorder)

                            Button("Browse...") {
                                let panel = NSSavePanel()
                                panel.directoryURL = viewModel.outputDirectory
                                panel.nameFieldStringValue = viewModel.baseName
                                panel.allowedContentTypes = [.movie]
                                panel.canCreateDirectories = true
                                if panel.runModal() == .OK, let url = panel.url {
                                    // Strip extension — RecordingEngine adds timestamp + ext
                                    let dir = url.deletingLastPathComponent().path
                                    let stem = url.deletingPathExtension().lastPathComponent
                                    viewModel.savePath = dir + "/" + stem
                                }
                            }
                        }
                    }

                    Spacer()
                }

                // Audio level meter
                if viewModel.selectedAudioDevice != nil {
                    AudioMeterView(monitor: viewModel.audioMonitor)
                }

                // Record button
                HStack {
                    Button(action: viewModel.toggleRecording) {
                        HStack(spacing: 8) {
                            Circle()
                                .fill(viewModel.recordingEngine.isRecording ? Color.gray : Color.red)
                                .frame(width: 16, height: 16)
                            Text(viewModel.recordingEngine.isRecording ? "STOP" : "RECORD")
                                .font(.system(size: 16, weight: .bold))
                        }
                        .frame(width: 160, height: 44)
                        .background(viewModel.recordingEngine.isRecording
                                    ? Color.gray.opacity(0.3)
                                    : Color.red.opacity(0.2))
                        .cornerRadius(8)
                    }
                    .buttonStyle(.plain)

                    Spacer()

                    // Status
                    VStack(alignment: .trailing) {
                        Text("\(viewModel.captureSession.activeWidth)x\(viewModel.captureSession.activeHeight)@\(String(format: "%.0f", viewModel.captureSession.activeFPS))fps")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(.secondary)
                        if viewModel.recordingEngine.isRecording {
                            Text("\(viewModel.recordingEngine.framesWritten) frames")
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }
            .padding()
        }
        .frame(minWidth: 700, minHeight: 500)
        .onAppear { viewModel.setup() }
    }
}
