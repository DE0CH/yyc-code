import Foundation

/// `/api/app-signin?state=S` ends at `dev.de0ch.yycnametags://signed-in?state=S#token=<CF_Authorization>`.
enum SignInCallback {
    /// 32 url-safe characters (the Worker accepts 16–128).
    static func newState() -> String {
        let chars = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        var g = SystemRandomNumberGenerator()
        return String((0..<32).map { _ in chars[Int(g.next(upperBound: UInt(chars.count)))] })
    }

    static func signInURL(base: URL, state: String) -> URL {
        var c = URLComponents(url: base.appendingPathComponent("api/app-signin"), resolvingAgainstBaseURL: false)!
        c.queryItems = [URLQueryItem(name: "state", value: state)]
        return c.url!
    }

    /// The token, if the callback is for this state.
    static func token(from url: URL, state: String) -> String? {
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false),
              c.queryItems?.first(where: { $0.name == "state" })?.value == state,
              let fragment = c.percentEncodedFragment else { return nil }
        for pair in fragment.split(separator: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1).map(String.init)
            if kv.count == 2, kv[0] == "token" {
                let t = kv[1].removingPercentEncoding ?? kv[1]
                return t.isEmpty ? nil : t
            }
        }
        return nil
    }
}
