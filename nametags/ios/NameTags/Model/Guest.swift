import Foundation

/// One row of the guest list, as `GET /api/guests` sends it (nametags/README.md). Fields are shown as they are in the
/// sheet; numbers (an id or a table number sent as a number) are read as text.
struct Guest: Identifiable, Hashable, Decodable {
    let id: String
    let name: String?          // column I (salutation + first name + surname)
    let institution: String?   // column L
    let salutation: String?
    let surname: String?
    let firstname: String?
    let title: String?
    let email: String?
    let mobile: String?
    let status: String?        // "Sent" | "Withdrawn" | null
    let table: String?

    enum CodingKeys: String, CodingKey {
        case id, name, institution, salutation, surname, firstname, title, email, mobile, status, table
    }

    init(id: String, name: String?, institution: String? = nil, salutation: String? = nil, surname: String? = nil,
         firstname: String? = nil, title: String? = nil, email: String? = nil, mobile: String? = nil,
         status: String? = nil, table: String? = nil) {
        self.id = id; self.name = name; self.institution = institution; self.salutation = salutation
        self.surname = surname; self.firstname = firstname; self.title = title; self.email = email
        self.mobile = mobile; self.status = status; self.table = table
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.text(.id) else {
            throw DecodingError.dataCorruptedError(forKey: .id, in: c, debugDescription: "guest without an id")
        }
        self.id = id
        name = c.text(.name); institution = c.text(.institution); salutation = c.text(.salutation)
        surname = c.text(.surname); firstname = c.text(.firstname); title = c.text(.title)
        email = c.text(.email); mobile = c.text(.mobile); status = c.text(.status); table = c.text(.table)
    }

    /// The name printed on the tag: column I, or the name parts when column I is empty.
    var displayName: String {
        if let name { return name }
        let parts = [salutation, firstname, surname].compactMap { $0 }
        return parts.isEmpty ? "（無名）" : parts.joined(separator: " ")
    }

    var isWithdrawn: Bool { status?.caseInsensitiveCompare("Withdrawn") == .orderedSame }

    /// Every field the search looks at.
    var searchFields: [String] {
        [name, institution, salutation, surname, firstname, title, email, mobile, status, table].compactMap { $0 }
    }
}

/// `GET /api/guests[?event=ID]` → the event's accepted guests; `event` names the event the list is for (the default
/// event when none was asked for).
struct GuestList: Decodable {
    let guests: [Guest]
    let updated: String?
    let event: String?

    enum CodingKeys: String, CodingKey { case guests, updated, event }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guests = try c.decode([Guest].self, forKey: .guests)
        updated = c.text(.updated)
        event = c.eventID(.event)
    }
}

struct PrinterStatus: Decodable, Equatable {
    let online: Bool
    let lastSeen: String?
    let printer: String?
    let error: String?
}

struct PrintJobCreated: Decodable { let id: String }

struct PrintJobStatus: Decodable {
    let id: String
    let status: String     // queued | printing | done | failed
    let error: String?
}

extension KeyedDecodingContainer {
    /// A string field that may come as a string or a number; empty or whitespace-only is nil.
    func text(_ key: Key) -> String? {
        var s: String?
        if let v = try? decodeIfPresent(String.self, forKey: key) { s = v }
        else if let v = try? decodeIfPresent(Int.self, forKey: key) { s = String(v) }
        else if let v = try? decodeIfPresent(Double.self, forKey: key) {
            s = v == v.rounded() && abs(v) < 1e15 ? String(Int(v)) : String(v)
        }
        guard let t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        return t
    }
}
