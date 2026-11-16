import SwiftUI

/// The name tag, laid out in label pixels: 848×590 = 72×50 mm at 300 dpi on the NIIMBOT M3's 75×50 mm label.
/// The same view is the preview (scaled down) and the print (ImageRenderer at scale 1, then 1-bit), so what staff
/// see is what prints. Layout follows Deyao's sample tag: name centred, institution centred below it, smaller,
/// regular weight, plenty of white; table number bottom-right when there is one.
struct NameTagView: View {
    static let size = CGSize(width: 848, height: 590)

    let name: String
    let institution: String?
    let table: String?

    init(guest: Guest) {
        name = guest.displayName
        institution = guest.institution
        table = guest.table
    }

    var body: some View {
        ZStack {
            Color.white
            VStack(spacing: 26) {
                FitText(text: name, sizes: [88, 80, 72, 64, 58], weight: .regular)
                if let institution {
                    FitText(text: institution, sizes: [50, 46, 42, 38, 34], weight: .regular)
                }
            }
            .padding(.horizontal, 44)
            .padding(.vertical, table == nil ? 40 : 84)
            if let table {
                VStack {
                    Spacer()
                    HStack {
                        Spacer()
                        Text("Table \(table)")
                            .font(.system(size: 40, weight: .semibold))
                    }
                }
                .padding(.horizontal, 40)
                .padding(.vertical, 30)
            }
        }
        .foregroundStyle(Color.black)
        .multilineTextAlignment(.center)
        .frame(width: Self.size.width, height: Self.size.height)
        .environment(\.colorScheme, .light)
    }
}

/// One line at the largest size that fits; when even the smallest doesn't, two lines at the smallest (shrunk further
/// only if two lines still don't fit).
private struct FitText: View {
    let text: String
    let sizes: [CGFloat]
    let weight: Font.Weight

    var body: some View {
        ViewThatFits(in: .horizontal) {
            ForEach(sizes, id: \.self) { s in
                Text(text).font(.system(size: s, weight: weight)).lineLimit(1)
            }
            Text(text)
                .font(.system(size: sizes.last!, weight: weight))
                .lineLimit(2)
                .minimumScaleFactor(0.6)
        }
    }
}

/// The tag at any width, for the detail page: the label's white with a hairline edge.
struct NameTagPreview: View {
    let guest: Guest

    var body: some View {
        GeometryReader { geo in
            let scale = geo.size.width / NameTagView.size.width
            NameTagView(guest: guest)
                .scaleEffect(scale, anchor: .topLeading)
                .frame(width: geo.size.width, height: geo.size.width * NameTagView.size.height / NameTagView.size.width,
                       alignment: .topLeading)
        }
        .aspectRatio(NameTagView.size.width / NameTagView.size.height, contentMode: .fit)
        .overlay(Rectangle().stroke(Theme.rule, lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("tagPreview")
    }
}

enum TagImage {
    /// The tag as the 848×590 1-bit PNG that `/api/print` takes.
    @MainActor
    static func png(for guest: Guest) -> Data? {
        let renderer = ImageRenderer(content: NameTagView(guest: guest))
        renderer.scale = 1
        renderer.isOpaque = true
        renderer.proposedSize = ProposedViewSize(NameTagView.size)
        guard let cg = renderer.cgImage else { return nil }
        let w = Int(NameTagView.size.width), h = Int(NameTagView.size.height)
        var gray = [UInt8](repeating: 255, count: w * h)
        let ok = gray.withUnsafeMutableBytes { buf -> Bool in
            guard let ctx = CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                      space: CGColorSpaceCreateDeviceGray(),
                                      bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            ctx.setFillColor(gray: 1, alpha: 1)
            ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
            ctx.interpolationQuality = .none
            ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
            return true
        }
        guard ok else { return nil }
        return PNG1Bit.encode(width: w, height: h, gray: gray)
    }
}
