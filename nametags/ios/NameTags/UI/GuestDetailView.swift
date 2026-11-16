import SwiftUI

enum PrintPhase: Equatable {
    case idle, sending, queued, printing, done
    case failed(String)

    /// Position on the line sending → queued → printing → done.
    var step: Int {
        switch self {
        case .idle: return -1
        case .sending: return 0
        case .queued: return 1
        case .printing: return 2
        case .done: return 3
        case .failed: return -1
        }
    }

    var busy: Bool { self == .sending || self == .queued || self == .printing }
}

struct GuestDetailView: View {
    let guest: Guest
    @EnvironmentObject private var model: AppModel
    @StateObject private var job = PrintJob()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack { Spacer(); PrinterBadge(status: model.printer) }
                NameTagPreview(guest: guest)
                if guest.isWithdrawn {
                    HStack(spacing: 8) {
                        WithdrawnLabel()
                        Text("此嘉賓已取消出席").font(.system(size: 15)).foregroundStyle(Theme.muted)
                    }
                }
                PrintPanel(job: job) { job.start(guest, model: model) }
                fields
            }
            .padding(16)
            .readableWidth()
        }
        .background(Theme.ground.ignoresSafeArea())
        .navigationTitle(guest.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .onDisappear { job.cancel() }
    }

    // MARK: fields

    private var fields: some View {
        let rows: [(String, String?)] = [
            ("稱謂", guest.salutation), ("名", guest.firstname), ("姓", guest.surname),
            ("機構", guest.institution), ("職位", guest.title), ("電郵", guest.email),
            ("手機", guest.mobile), ("狀態", guest.status), ("枱號", guest.table),
        ]
        return VStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { i, r in
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(r.0)
                        .font(.system(size: 15))
                        .foregroundStyle(Theme.muted)
                        .frame(width: 44, alignment: .leading)
                    Text(r.1 ?? "—")
                        .font(.system(size: 16))
                        .foregroundStyle(r.1 == nil ? Theme.muted : Theme.ink)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
                if i < rows.count - 1 { Rectangle().fill(Theme.rule).frame(height: 1).padding(.leading, 16) }
            }
        }
        .background(Theme.panel)
    }
}

/// The print job drawn as a line diagram: four square nodes 傳送 → 排隊 → 打印 → 完成, ink-filled up to where the job
/// is; a failure shows a dashed node with a cross where it stopped.
struct PrintLine: View {
    let phase: PrintPhase
    let failedAt: Int
    private let labels = ["傳送", "排隊", "打印", "完成"]

    var body: some View {
        let failed: Bool = { if case .failed = phase { return true }; return false }()
        let reached = failed ? failedAt - 1 : phase.step
        HStack(spacing: 0) {
            ForEach(0..<4, id: \.self) { i in
                VStack(spacing: 6) {
                    HStack(spacing: 0) {
                        Rectangle().fill(i == 0 ? Color.clear : (i <= reached || (failed && i == failedAt) ? Theme.ink : Theme.rule))
                            .frame(height: 4)
                        node(i, reached: reached, failed: failed)
                        Rectangle().fill(i == 3 ? Color.clear : (i < reached ? Theme.ink : Theme.rule))
                            .frame(height: 4)
                    }
                    Text(labels[i])
                        .font(.system(size: 13, weight: i == reached && !failed ? .semibold : .regular))
                        .foregroundStyle(i <= reached || (failed && i == failedAt) ? Theme.ink : Theme.muted)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .accessibilityHidden(true)
    }

    @ViewBuilder
    private func node(_ i: Int, reached: Int, failed: Bool) -> some View {
        if failed && i == failedAt {
            CrossIcon()
                .foregroundStyle(Theme.ink)
                .padding(4)
                .frame(width: 18, height: 18)
                .background(Theme.panel)
                .overlay(Rectangle().stroke(Theme.ink, style: StrokeStyle(lineWidth: 2, dash: [3, 2])))
        } else {
            Rectangle()
                .fill(i <= reached ? Theme.ink : Theme.panel)
                .overlay(Rectangle().stroke(i <= reached ? Theme.ink : Theme.rule, lineWidth: 3))
                .frame(width: 18, height: 18)
        }
    }
}
