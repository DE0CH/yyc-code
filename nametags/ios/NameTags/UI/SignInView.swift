import SwiftUI

struct SignInView: View {
    let expired: Bool
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Spacer()
            Text("名牌")
                .font(.system(size: 44, weight: .heavy))
                .foregroundStyle(Theme.ink)
            Text("YYC 嘉賓名牌打印")
                .font(.system(size: 17))
                .foregroundStyle(Theme.muted)
                .padding(.top, 4)
            VStack(alignment: .leading, spacing: 16) {
                Text(expired ? "登入已過期，請重新登入。" : "用 YYC 帳戶登入後，便可搜尋嘉賓及打印名牌。")
                    .font(.system(size: 17))
                    .foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("signInMessage")
                if let err = model.signInError {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        CrossIcon().frame(width: 14, height: 14)
                        Text(err).font(.system(size: 15))
                    }
                    .foregroundStyle(Theme.ink)
                }
                Button {
                    Task { await model.signIn() }
                } label: {
                    Text(model.signingIn ? "登入中…" : (expired ? "重新登入" : "登入"))
                }
                .buttonStyle(BlockButtonStyle())
                .disabled(model.signingIn)
                .accessibilityIdentifier("signInButton")
            }
            .padding(20)
            .background(Theme.panel)
            .padding(.top, 28)
            Spacer()
            Spacer()
        }
        .padding(.horizontal, 16)
        .frame(maxWidth: 520)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.ground.ignoresSafeArea())
    }
}

/// The house cross (failed): two square-capped strokes.
struct CrossIcon: View {
    var body: some View {
        GeometryReader { g in
            Path { p in
                p.move(to: .zero); p.addLine(to: CGPoint(x: g.size.width, y: g.size.height))
                p.move(to: CGPoint(x: g.size.width, y: 0)); p.addLine(to: CGPoint(x: 0, y: g.size.height))
            }
            .stroke(style: StrokeStyle(lineWidth: 2, lineCap: .square))
        }
    }
}
