import Foundation

enum APIError: Error, LocalizedError {
    case unauthorized                 // 401/403 or Access' login page: sign in again
    case http(Int, String)
    case network(String)

    var errorDescription: String? {
        switch self {
        case .unauthorized: return "登入已過期"
        case .http(let code, let body): return body.isEmpty ? "伺服器錯誤 \(code)" : "伺服器錯誤 \(code)：\(body)"
        case .network(let m): return "網絡錯誤：\(m)"
        }
    }
}

/// The Worker's API (nametags/README.md), every call carrying the Access token as `cf-access-token`.
struct API {
    let base: URL
    let token: String

    private func send(_ path: String, query: [URLQueryItem] = [], method: String = "GET",
                      body: Data? = nil, contentType: String? = nil) async throws -> Data {
        var comps = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query }
        var req = URLRequest(url: comps.url!, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
        req.httpMethod = method
        req.setValue(token, forHTTPHeaderField: "cf-access-token")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let contentType { req.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        req.httpBody = body
        let data: Data, resp: URLResponse
        do { (data, resp) = try await URLSession.shared.data(for: req) }
        catch { throw APIError.network(error.localizedDescription) }
        guard let http = resp as? HTTPURLResponse else { throw APIError.network("no HTTP response") }
        if http.statusCode == 401 || http.statusCode == 403 { throw APIError.unauthorized }
        // An expired token gets redirected to Access' login page, which comes back as HTML.
        let type = http.value(forHTTPHeaderField: "Content-Type") ?? ""
        if type.contains("text/html") || (http.url?.host ?? "").hasSuffix("cloudflareaccess.com") {
            throw APIError.unauthorized
        }
        guard (200..<300).contains(http.statusCode) else {
            let text = String(data: data.prefix(300), encoding: .utf8) ?? ""
            let msg = (try? JSONDecoder().decode([String: String].self, from: data))?["error"] ?? text
            throw APIError.http(http.statusCode, msg)
        }
        return data
    }

    private func decode<T: Decodable>(_ t: T.Type, _ data: Data) throws -> T {
        do { return try JSONDecoder().decode(t, from: data) }
        catch { throw APIError.network("回應格式不對（\(error.localizedDescription)）") }
    }

    /// The event's guest list; `event == nil` asks for the server's default event.
    func guests(event: String?) async throws -> GuestList {
        let q = event.map { [URLQueryItem(name: "event", value: $0)] } ?? []
        return try decode(GuestList.self, try await send("api/guests", query: q))
    }

    func events() async throws -> [Event] { try decode(EventList.self, try await send("api/events")).events }

    /// The registration a scanned QR code belongs to. 404 (unknown code) and 409 (not accepted) carry the server's
    /// text for staff as `APIError.http`.
    func checkin(code: String) async throws -> Checkin {
        try decode(Checkin.self, try await send("api/checkin", query: [URLQueryItem(name: "code", value: code)]))
    }

    func printer() async throws -> PrinterStatus { try decode(PrinterStatus.self, try await send("api/printer")) }

    func print(png: Data, guest: String) async throws -> String {
        let d = try await send("api/print", query: [URLQueryItem(name: "guest", value: guest)], method: "POST",
                               body: png, contentType: "image/png")
        return try decode(PrintJobCreated.self, d).id
    }

    func job(_ id: String) async throws -> PrintJobStatus {
        try decode(PrintJobStatus.self, try await send("api/print/\(id)"))
    }
}
