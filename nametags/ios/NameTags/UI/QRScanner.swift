import AVFoundation
import SwiftUI
import UIKit

/// The back camera reading QR codes (AVCaptureMetadataOutput). `onCode` gets each code read while running, on the
/// main thread; the owner stops it once it has one.
@MainActor
final class QRScanner: NSObject, ObservableObject, AVCaptureMetadataOutputObjectsDelegate {
    enum Status: Equatable { case idle, ready, noCamera, denied }

    @Published private(set) var status: Status = .idle
    let session = AVCaptureSession()
    var onCode: (@MainActor (String) -> Void)?

    private let queue = DispatchQueue(label: "dev.de0ch.yycnametags.camera")
    private var wantRunning = false

    /// Asks for the camera (the first time) and sets up the session.
    func prepare() async {
        guard status == .idle else { return }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: break
        case .notDetermined:
            guard await AVCaptureDevice.requestAccess(for: .video) else { status = .denied; return }
        default:
            status = .denied; return
        }
        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else { status = .noCamera; return }
        let output = AVCaptureMetadataOutput()
        session.beginConfiguration()
        session.addInput(input)
        guard session.canAddOutput(output) else {
            session.commitConfiguration(); status = .noCamera; return
        }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        if output.availableMetadataObjectTypes.contains(.qr) { output.metadataObjectTypes = [.qr] }
        session.commitConfiguration()
        status = .ready
        if wantRunning { run(true) }
    }

    func setRunning(_ on: Bool) {
        wantRunning = on
        if status == .ready { run(on) }
    }

    /// startRunning blocks for a while: on the camera's own queue.
    private func run(_ on: Bool) {
        let session = self.session
        queue.async {
            if on, !session.isRunning { session.startRunning() }
            if !on, session.isRunning { session.stopRunning() }
        }
    }

    nonisolated func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject],
                                    from connection: AVCaptureConnection) {
        let codes = metadataObjects.compactMap { ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }
        guard let code = codes.first else { return }
        // Delivered on the main queue (setMetadataObjectsDelegate above).
        MainActor.assumeIsolated {
            guard wantRunning else { return }
            onCode?(code)
        }
    }
}

/// The camera picture, filling its frame.
struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let v = PreviewView()
        v.previewLayer.session = session
        v.previewLayer.videoGravity = .resizeAspectFill
        v.backgroundColor = .black
        return v
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }
}
