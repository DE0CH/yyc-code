import SwiftUI

/// ■ 打印機在線 / □ 打印機離線, from `/api/printer` (online = the Mac bridge polled within 10 s).
struct PrinterBadge: View {
    let status: PrinterStatus?

    private var text: String {
        guard let status else { return "打印機狀態不明" }
        if !status.online { return "打印機離線" }
        if let e = status.error, !e.isEmpty { return "打印機：\(e)" }
        return "打印機在線"
    }

    private var good: Bool { status?.online == true && (status?.error ?? "").isEmpty }

    var body: some View {
        HStack(spacing: 8) {
            Pip(on: good)
            Text(text)
                .font(.system(size: 15, weight: good ? .semibold : .regular))
                .foregroundStyle(good ? Theme.ink : Theme.muted)
                .lineLimit(1)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
        .accessibilityIdentifier("printerStatus")
    }
}
