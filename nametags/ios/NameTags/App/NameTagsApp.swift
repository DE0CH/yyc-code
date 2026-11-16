import SwiftUI

@main
struct NameTagsApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .tint(Theme.ink)
                .transaction { $0.animation = nil }   // house style: no motion
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        switch model.auth {
        case .signedOut: SignInView(expired: false)
        case .expired: SignInView(expired: true)
        case .signedIn(let token): SignedInView().id(token)
        }
    }
}

struct SignedInView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            GuestListView()
                .navigationDestination(for: Guest.self) { GuestDetailView(guest: $0) }
        }
        .task { await model.loadGuests() }
        .task { await model.loadEvents() }
        .task { await model.watchPrinter() }
    }
}
