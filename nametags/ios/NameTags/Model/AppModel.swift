import AuthenticationServices
import Foundation
import os
import RomanSearch
import UIKit

let appLog = Logger(subsystem: "dev.de0ch.yycnametags", category: "app")

/// Sign-in state, the events, the chosen event's guest list and the printer's status.
@MainActor
final class AppModel: ObservableObject {
    enum Auth: Equatable { case signedOut, expired, signedIn(String) }

    @Published private(set) var auth: Auth
    @Published private(set) var signingIn = false
    @Published var signInError: String?

    @Published private(set) var guests: [Guest] = []
    @Published private(set) var loadingGuests = false
    @Published private(set) var guestsError: String?
    @Published private(set) var printer: PrinterStatus?

    /// Every event (empty until loaded, or from a server without `/api/events`: then there is no picker).
    @Published private(set) var events: [Event] = []
    /// The event the guest list is for, as the server named it (nil: not loaded yet, or the server didn't say).
    @Published private(set) var listEvent: String?

    private var index: GuestSearch?
    private var loadGeneration = 0
    /// The event staff last picked; none = the server's default, which moves on as events pass.
    private static let chosenEventKey = "chosenEvent"
    private let webAuth = WebAuth()

    init() {
        if let t = Config.testToken {
            let s = Keychain.save(t)
            appLog.info("test token from launch arguments, keychain status \(s)")
            auth = .signedIn(t)
        } else if let t = Keychain.load() {
            auth = .signedIn(t)
        } else {
            auth = .signedOut
        }
    }

    var api: API? {
        if case .signedIn(let t) = auth { return API(base: Config.baseURL, token: t) }
        return nil
    }

    // MARK: sign-in

    func signIn() async {
        guard !signingIn else { return }
        signingIn = true; signInError = nil
        defer { signingIn = false }
        let state = SignInCallback.newState()
        let url = SignInCallback.signInURL(base: Config.baseURL, state: state)
        do {
            let callback = try await webAuth.start(url: url, scheme: Config.callbackScheme)
            guard let token = SignInCallback.token(from: callback, state: state) else {
                signInError = "登入沒有完成，請再試。"
                return
            }
            let s = Keychain.save(token)
            if s != errSecSuccess { appLog.error("keychain save failed: \(s)") }
            guestsError = nil
            auth = .signedIn(token)
        } catch let e as ASWebAuthenticationSessionError where e.code == .canceledLogin {
            return
        } catch {
            signInError = "登入失敗：\(error.localizedDescription)"
        }
    }

    func signOut() {
        Keychain.delete()
        reset()
        auth = .signedOut
    }

    /// The server refused the token (expired or revoked): back to the sign-in screen, saying why.
    func expire() {
        Keychain.delete()
        reset()
        auth = .expired
    }

    private func reset() {
        guests = []; index = nil; printer = nil; guestsError = nil; events = []; listEvent = nil
        loadGeneration += 1
    }

    /// Runs `body`; a refused token signs the app out as expired. Other errors are returned to the caller.
    func call<T>(_ body: (API) async throws -> T) async throws -> T? {
        guard let api else { return nil }
        do { return try await body(api) }
        catch APIError.unauthorized { expire(); return nil }
    }

    // MARK: guests

    private var chosenEvent: String? {
        get { UserDefaults.standard.string(forKey: Self.chosenEventKey) }
        set { UserDefaults.standard.set(newValue, forKey: Self.chosenEventKey) }
    }

    /// The event shown in the picker: the list's own, else the remembered choice, else the server's default rule.
    var currentEvent: Event? {
        let id = listEvent ?? chosenEvent
        if let id { return events.first { $0.id == id } }
        return Event.serverDefault(events)
    }

    func loadEvents() async {
        do {
            if let list = try await call({ try await $0.events() }) {
                events = list.sorted { ($0.date ?? "", $0.id) > ($1.date ?? "", $1.id) }   // newest first
            }
        } catch {
            appLog.error("events: \(error.localizedDescription)")   // an older server: no picker, the default list
        }
    }

    /// Staff picked an event: remembered, and its list loaded.
    func choose(_ event: Event) {
        chosenEvent = event.id
        guard event.id != listEvent else { return }
        guests = []; index = nil; guestsError = nil; listEvent = event.id
        Task { await loadGuests() }
    }

    /// Loads the list for the chosen event (the server's default when none is chosen). A later load supersedes it.
    func loadGuests() async {
        loadGeneration += 1
        let generation = loadGeneration
        loadingGuests = true
        defer { if generation == loadGeneration { loadingGuests = false } }
        let wanted = chosenEvent
        do {
            var list: GuestList?
            do {
                list = try await call({ try await $0.guests(event: wanted) })
            } catch APIError.http(404, _) where wanted != nil {
                // The remembered event is gone: back to the default.
                appLog.info("chosen event \(wanted ?? "") not found: using the default")
                if generation == loadGeneration { chosenEvent = nil }
                list = try await call({ try await $0.guests(event: nil) })
            }
            guard let list, generation == loadGeneration else { return }
            // The first build loads the romanisation table (~0.3 s): off the main thread.
            let fields = list.guests.map(\.searchFields)
            let built = await Task.detached(priority: .userInitiated) { GuestSearch(fields) }.value
            guard generation == loadGeneration else { return }
            guests = list.guests
            index = built
            listEvent = list.event ?? chosenEvent
            guestsError = nil
        } catch {
            if generation == loadGeneration { guestsError = error.localizedDescription }
        }
    }

    func results(for query: String) -> [Guest] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, let index else { return guests }
        return index.search(q).compactMap { $0 < guests.count ? guests[$0] : nil }
    }

    // MARK: printer

    func refreshPrinter() async {
        do {
            if let p = try await call({ try await $0.printer() }) { printer = p }
        } catch {
            appLog.error("printer status: \(error.localizedDescription)")
            printer = nil
        }
    }

    /// Polls the printer's status while the signed-in screens are up.
    func watchPrinter() async {
        while !Task.isCancelled {
            await refreshPrinter()
            try? await Task.sleep(nanoseconds: 5_000_000_000)
        }
    }
}

/// ASWebAuthenticationSession as an async call. Not ephemeral: Safari keeps the Access login, so signing in again
/// after the token expires is usually one tap.
final class WebAuth: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?

    @MainActor
    func start(url: URL, scheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<URL, Error>) in
            let s = ASWebAuthenticationSession(url: url, callbackURLScheme: scheme) { [weak self] url, error in
                self?.session = nil
                if let url { cont.resume(returning: url) }
                else { cont.resume(throwing: error ?? URLError(.cancelled)) }
            }
            s.presentationContextProvider = self
            s.prefersEphemeralWebBrowserSession = false
            session = s
            if !s.start() {
                session = nil
                cont.resume(throwing: URLError(.cannotLoadFromNetwork))
            }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
        return windows.first(where: \.isKeyWindow) ?? windows.first ?? ASPresentationAnchor()
    }
}
