import SwiftUI
import UIKit

/// 掃描: the camera reads a guest's registration QR, `GET /api/checkin` finds the guest, and the tag prints at once
/// (the same job and states as the detail page's Print). An unknown or unconfirmed code shows the server's text.
struct ScanView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @StateObject private var scanner = QRScanner()
    @StateObject private var job = PrintJob()
    @State private var phase: Phase = .scanning
    @State private var lookup: Task<Void, Never>?
    @State private var lastRead: String?
    @State private var rearmed = Date.distantPast

    enum Phase: Equatable {
        case scanning
        case looking
        case found(Guest, event: String?)
        case failed(String)
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    content
                }
                .padding(16)
                .readableWidth()
            }
        }
        .background(Theme.ground.ignoresSafeArea())
        .task {
            scanner.onCode = { code in
                // The last guest's QR still in front of the camera right after 掃描下一位 is not a new scan.
                if code == lastRead && Date().timeIntervalSince(rearmed) < 3 { return }
                read(code)
            }
            if let code = Config.scanCode {
                read(code)
            } else {
                await scanner.prepare()
                scanner.setRunning(phase == .scanning)
            }
        }
        .onChange(of: phase) { _, p in scanner.setRunning(p == .scanning) }
        .onDisappear {
            scanner.setRunning(false)
            lookup?.cancel()
            job.cancel()
        }
    }

    private var header: some View {
        HStack(alignment: .center) {
            Text("掃描")
                .font(.system(size: 28, weight: .heavy))
                .foregroundStyle(Theme.ink)
                .accessibilityAddTraits(.isHeader)
            Spacer()
            PrinterBadge(status: model.printer)
            Button { dismiss() } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Theme.ink)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel("關閉")
            .accessibilityIdentifier("closeScan")
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 4)
        .readableWidth()
    }

    @ViewBuilder
    private var content: some View {
        switch phase {
        case .scanning:
            camera
        case .looking:
            note("查詢中…")
        case .found(let guest, let event):
            NameTagPreview(guest: guest)
            if let event, let current = model.currentEvent?.id, event != current {
                note("此嘉賓屬於另一活動：\(model.events.first { $0.id == event }?.name ?? event)")
            }
            if guest.isWithdrawn {
                HStack(spacing: 8) {
                    WithdrawnLabel()
                    Text("此嘉賓已取消出席").font(.system(size: 15)).foregroundStyle(Theme.muted)
                }
            }
            PrintPanel(job: job) { job.start(guest, model: model) }
            Button("掃描下一位") { scanAgain() }
                .buttonStyle(BlockButtonStyle(primary: false))
                .disabled(job.phase.busy)
                .accessibilityIdentifier("scanNext")
        case .failed(let message):
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                CrossIcon().frame(width: 14, height: 14)
                Text(message)
                    .font(.system(size: 17, weight: .semibold))
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("scanError")
            }
            .foregroundStyle(Theme.ink)
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel)
            Button("再掃描") { scanAgain() }
                .buttonStyle(BlockButtonStyle())
                .accessibilityIdentifier("scanAgain")
        }
    }

    @ViewBuilder
    private var camera: some View {
        switch scanner.status {
        case .denied:
            VStack(alignment: .leading, spacing: 12) {
                Text("未允許使用相機。請到「設定」>「名牌」開啟相機。")
                    .font(.system(size: 17))
                    .fixedSize(horizontal: false, vertical: true)
                Button("打開設定") {
                    if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }
                }
                .buttonStyle(BlockButtonStyle(primary: false))
            }
            .foregroundStyle(Theme.ink)
            .padding(16)
            .background(Theme.panel)
        case .noCamera:
            note("此裝置沒有可用的相機")
        case .idle, .ready:
            CameraPreview(session: scanner.session)
                .aspectRatio(1, contentMode: .fit)
                .overlay(Rectangle().stroke(Theme.ink, lineWidth: 3))
                .accessibilityLabel("相機")
                .accessibilityIdentifier("camera")
            Text("把嘉賓的報名二維碼放進方框")
                .font(.system(size: 15))
                .foregroundStyle(Theme.muted)
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 15))
            .foregroundStyle(Theme.muted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel)
            .accessibilityIdentifier("scanNote")
    }

    // MARK: flow

    /// A code from the camera (or the CI hook): look it up, then print at once.
    private func read(_ scanned: String) {
        guard phase == .scanning else { return }
        lastRead = scanned
        guard let code = CheckinCode.parse(scanned) else {
            appLog.info("scanned a code that isn't a registration: \(scanned, privacy: .private)")
            phase = .failed("這不是報名二維碼")
            return
        }
        phase = .looking
        lookup?.cancel()
        lookup = Task {
            do {
                guard let found = try await model.call({ try await $0.checkin(code: code) }) else { return }
                if Task.isCancelled { return }
                phase = .found(found.guest, event: found.event)
                job.start(found.guest, model: model)
            } catch APIError.http(let status, let message) where status == 404 || status == 409 {
                phase = .failed(message.isEmpty ? (status == 404 ? "找不到此二維碼" : "此報名未確認") : message)
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func scanAgain() {
        job.reset()
        lookup?.cancel()
        phase = .scanning
        rearmed = Date()
        if let code = Config.scanCode { read(code) }
    }
}
