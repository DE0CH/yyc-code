import Foundation
import XCTest
@testable import RomanSearch

/// Every guest of a real sheet is found by its surname and by its full name. The data stays out of git:
/// `Scripts/export-guests.py guests.xlsx /tmp/guests.json` then `ROMANSEARCH_GUESTS_JSON=/tmp/guests.json swift test`.
final class RealGuestsTests: XCTestCase {
    struct Guest: Decodable { let fields: [String]; let surname: String }

    func testEveryGuestFoundBySurnameAndFullName() throws {
        guard let path = ProcessInfo.processInfo.environment["ROMANSEARCH_GUESTS_JSON"] else {
            throw XCTSkip("ROMANSEARCH_GUESTS_JSON not set")
        }
        let guests = try JSONDecoder().decode([Guest].self, from: Data(contentsOf: URL(fileURLWithPath: path)))
        let engine = GuestSearch(guests.map(\.fields))
        var fullNameFirst = 0
        for (i, g) in guests.enumerated() {
            let bySurname = engine.search(g.surname)
            XCTAssertTrue(bySurname.contains(i), "guest \(i) not found by its surname")
            let byName = engine.search(g.fields[0])
            XCTAssertTrue(byName.contains(i), "guest \(i) not found by its full name")
            // Several guests may share a name (a guest listed twice); the guest must rank among those.
            let tied = guests.indices.filter { Text.fold(guests[$0].fields[0]).split(separator: " ") == Text.fold(g.fields[0]).split(separator: " ") }
            if Set(byName.prefix(tied.count)).contains(i) { fullNameFirst += 1 }
            XCTAssertTrue(Set(byName.prefix(tied.count)).contains(i), "guest \(i) not ranked first by its full name")
        }
        print("real guests: \(guests.count) found by surname and full name; \(fullNameFirst) ranked first by full name")
    }
}
