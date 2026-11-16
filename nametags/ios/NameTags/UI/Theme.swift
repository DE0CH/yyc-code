import SwiftUI
import UIKit

/// deyao-web-design tokens: ink on white sheets over a grey ground, square edges, no hue except for meaning (this
/// app has no category that needs one: states are ink-filled vs hollow squares).
enum Theme {
    static let ground = dyn(0xEEF0F2, 0x111315)
    static let panel = dyn(0xFFFFFF, 0x1A1D20)
    static let ink = dyn(0x14171A, 0xE8EAEC)
    static let muted = dyn(0x59626B, 0x9EA6AE)
    static let rule = dyn(0xD3D8DD, 0x33393F)
    static let onInk = dyn(0xFFFFFF, 0x111315)

    private static func dyn(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(uiColor: UIColor { $0.userInterfaceStyle == .dark ? rgb(dark) : rgb(light) })
    }

    private static func rgb(_ v: UInt32) -> UIColor {
        UIColor(red: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                blue: CGFloat(v & 0xFF) / 255, alpha: 1)
    }
}

/// A full-width (or, not `wide`, content-width) button: ink-filled (primary) or ink-outlined; pressed = instantly
/// inverted. No motion.
struct BlockButtonStyle: ButtonStyle {
    var primary = true
    var wide = true
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        let filled = primary != configuration.isPressed
        configuration.label
            .font(.system(size: 17, weight: .semibold))
            .padding(.horizontal, wide ? 0 : 14)
            .frame(maxWidth: wide ? .infinity : nil, minHeight: wide ? 50 : 48)
            .foregroundStyle(filled ? Theme.onInk : Theme.ink)
            .background(filled ? (enabled ? Theme.ink : Theme.muted) : Theme.panel)
            .overlay(Rectangle().stroke(enabled ? Theme.ink : Theme.muted, lineWidth: 2))
            .contentShape(Rectangle())
    }
}

/// A list row that fills with the ground colour while pressed.
struct RowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(configuration.isPressed ? Theme.ground : Theme.panel)
            .contentShape(Rectangle())
    }
}

/// ■ filled = good, □ hollow = not (the skill's verdict pip).
struct Pip: View {
    let on: Bool
    var size: CGFloat = 12
    var body: some View {
        Rectangle()
            .fill(on ? Theme.ink : Color.clear)
            .overlay(Rectangle().stroke(on ? Theme.ink : Theme.muted, lineWidth: 2))
            .frame(width: size, height: size)
    }
}

extension View {
    /// Content no wider than a comfortable reading width on iPad.
    func readableWidth() -> some View {
        frame(maxWidth: 720).frame(maxWidth: .infinity)
    }
}
