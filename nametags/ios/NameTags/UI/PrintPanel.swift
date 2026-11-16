import SwiftUI

/// One tag's print job: render, `POST /api/print`, then poll `/api/print/:id` until done or failed. Shared by the
/// detail page's Print and the scanner's print-at-once.
@MainActor
final class PrintJob: ObservableObject {
    @Published private(set) var phase: PrintPhase = .idle
    @Published private(set) var failedAt = 0
    private var task: Task<Void, Never>?

    func start(_ guest: Guest, model: AppModel) {
        task?.cancel()
        task = Task { await run(guest, model: model) }
    }

    /// Stops watching (the job itself stays on the server).
    func cancel() { task?.cancel() }

    func reset() {
        cancel()
        phase = .idle; failedAt = 0
    }

    private func fail(_ msg: String, at step: Int) {
        failedAt = max(step, 0)
        phase = .failed(msg)
    }

    private func run(_ guest: Guest, model: AppModel) async {
        phase = .sending
        guard let png = TagImage.png(for: guest) else { fail("無法產生名牌圖像", at: 0); return }
        let jobID: String
        do {
            guard let id = try await model.call({ try await $0.print(png: png, guest: guest.id) }) else { return }
            jobID = id
        } catch {
            fail(error.localizedDescription, at: 0); return
        }
        phase = .queued
        // The bridge polls every 2 s; give up watching after 10 minutes (the job stays queued on the server).
        let deadline = Date().addingTimeInterval(600)
        var lastStep = 1
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            if Task.isCancelled { return }
            do {
                guard let job = try await model.call({ try await $0.job(jobID) }) else { return }
                switch job.status {
                case "queued": phase = .queued; lastStep = 1
                case "printing": phase = .printing; lastStep = 2
                case "done": phase = .done; return
                case "failed": fail(job.error ?? "打印機出錯", at: lastStep + 1 > 3 ? 3 : lastStep + 1); return
                default: break
                }
            } catch {
                appLog.error("job \(jobID): \(error.localizedDescription)")   // keep polling through a network blip
            }
            if Date() > deadline { fail("等了 10 分鐘仍未打印，請檢查打印機", at: lastStep); return }
        }
    }
}

/// The print line, the job's status text and the Print button (打印 / 再印一張 / 再試).
struct PrintPanel: View {
    @ObservedObject var job: PrintJob
    let action: () -> Void
    @EnvironmentObject private var model: AppModel

    var body: some View {
        let phase = job.phase
        VStack(alignment: .leading, spacing: 14) {
            if phase != .idle {
                PrintLine(phase: phase, failedAt: job.failedAt)
                Text(statusText(phase))
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("printStatus")
            }
            Button(buttonTitle(phase), action: action)
                .buttonStyle(BlockButtonStyle(primary: phase == .idle || phase.busy))
                .disabled(phase.busy)
                .accessibilityIdentifier("printButton")
        }
        .padding(16)
        .background(Theme.panel)
    }

    private func buttonTitle(_ phase: PrintPhase) -> String {
        switch phase {
        case .idle: return "打印"
        case .sending: return "傳送中…"
        case .queued, .printing: return "打印中…"
        case .done: return "再印一張"
        case .failed: return "再試"
        }
    }

    private func statusText(_ phase: PrintPhase) -> String {
        switch phase {
        case .idle: return ""
        case .sending: return "傳送中"
        case .queued:
            return model.printer?.online == false ? "已排隊：打印機離線，連線後會自動打印" : "已排隊，等待打印機"
        case .printing: return "打印中"
        case .done: return "已打印"
        case .failed(let e): return "打印失敗：\(e)"
        }
    }
}
