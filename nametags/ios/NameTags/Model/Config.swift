import Foundation

enum Config {
    static let callbackScheme = "dev.de0ch.yycnametags"

    /// The server. The CI replica points the app at its stub server with the launch argument `-apiBase URL`
    /// (UserDefaults' argument domain); nothing in the app sets it.
    static var baseURL: URL {
        if let s = UserDefaults.standard.string(forKey: "apiBase"), let u = URL(string: s), u.scheme != nil { return u }
        return URL(string: "https://tags.yyc-internal.com")!
    }

    /// CI only: `-testToken T` signs the app in with T (the stub server's token) instead of the web sign-in.
    static var testToken: String? {
        guard let t = UserDefaults.standard.string(forKey: "testToken"), !t.isEmpty else { return nil }
        return t
    }

    /// CI only (the simulator has no camera): `-scanCode C` is what the scanner reads every time it starts, so the scan
    /// path runs from the parse onwards exactly as with the camera.
    static var scanCode: String? {
        guard let c = UserDefaults.standard.string(forKey: "scanCode"), !c.isEmpty else { return nil }
        return c
    }
}
