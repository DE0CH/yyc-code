import Foundation

/// What a registration QR code holds: `https://register.yyc-internal.com/r/<code>` (nametags/README.md), or the bare
/// code. Anything else isn't one of ours.
enum CheckinCode {
    static let host = "register.yyc-internal.com"

    static func parse(_ scanned: String) -> String? {
        let s = scanned.trimmingCharacters(in: .whitespacesAndNewlines)
        if isCode(s) { return s }
        guard let u = URLComponents(string: s), u.scheme?.lowercased() == "https", u.host?.lowercased() == host
        else { return nil }
        let parts = u.path.split(separator: "/").map(String.init)
        guard parts.count == 2, parts[0] == "r", isCode(parts[1]) else { return nil }
        return parts[1]
    }

    /// The server's codes are 22 url-safe characters; any url-safe run of 8–64 is passed on and the server decides.
    static func isCode(_ s: String) -> Bool {
        (8...64).contains(s.count)
            && s.unicodeScalars.allSatisfy { $0.isASCII && (CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_") }
    }
}
