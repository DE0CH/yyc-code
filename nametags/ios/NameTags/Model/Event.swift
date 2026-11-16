import Foundation

/// An event, as `GET /api/events` sends it (nametags/README.md; the staff counts are not read).
struct Event: Identifiable, Hashable, Decodable {
    let id: String
    let name: String
    let nameEn: String?
    let date: String?      // YYYY-MM-DD
    let venue: String?

    enum CodingKeys: String, CodingKey { case id, name, nameEn, date, venue }

    init(id: String, name: String, nameEn: String? = nil, date: String? = nil, venue: String? = nil) {
        self.id = id; self.name = name; self.nameEn = nameEn; self.date = date; self.venue = venue
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.text(.id) else {
            throw DecodingError.dataCorruptedError(forKey: .id, in: c, debugDescription: "event without an id")
        }
        self.id = id
        name = c.text(.name) ?? id
        nameEn = c.text(.nameEn); date = c.text(.date); venue = c.text(.venue)
    }

    /// The line under the name: date · venue.
    var detail: String? {
        let parts = [date, venue].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The event `GET /api/guests` without `event` serves: the nearest by date that is today or later (Hong Kong
    /// time), else the latest. Only used when the server's guest list doesn't name its event.
    static func serverDefault(_ events: [Event], now: Date = Date()) -> Event? {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "Asia/Hong_Kong")
        f.dateFormat = "yyyy-MM-dd"
        let today = f.string(from: now)
        let dated = events.filter { $0.date != nil }
        if let next = dated.filter({ $0.date! >= today }).min(by: { $0.date! < $1.date! }) { return next }
        return dated.max(by: { $0.date! < $1.date! }) ?? events.first
    }
}

struct EventList: Decodable {
    let events: [Event]
}

/// `GET /api/checkin?code=C` → `{event, guest}` for an accepted registration.
struct Checkin: Decodable {
    let event: String?
    let guest: Guest

    enum CodingKeys: String, CodingKey { case event, guest }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guest = try c.decode(Guest.self, forKey: .guest)
        event = c.eventID(.event)
    }
}

private struct EventRef: Decodable { let id: String }

extension KeyedDecodingContainer {
    /// An event named by its id, or given as an object with an `id`.
    func eventID(_ key: Key) -> String? {
        if let s = text(key) { return s }
        return (try? decodeIfPresent(EventRef.self, forKey: key))?.id
    }
}
