import Foundation

/// Local, instant guest search for the name-tag app.
///
/// Each guest is its list of searchable fields, name first. `search` returns the indexes of the matching guests,
/// best match first (ties keep the input order); an empty query returns every guest in order.
///
/// Every query word must match. Latin words match a field word exactly, as a prefix or (2+ letters) as a substring,
/// the name's initials ("kco" -> Ki Cheung Oliver), or a field with its spaces removed; in the name they also match
/// the spellings of the same syllable in Jyutping, HK government romanisation, Hanyu Pinyin and Wade-Giles
/// ("zoeng"/"zhang" -> Cheung, "hsu" -> Xu). Chinese characters match the same character in any field (simplified and
/// traditional alike) or, through every reading, a romanised name word (黃 -> Wong, Huang; 黃靜 -> Jing HUANG);
/// several characters may also spell one name word together (子怡 -> Ziyi). Case, diacritics and width are ignored.
public struct GuestSearch: Sendable {
    typealias Bytes = [UInt8]

    private let guests: [Indexed]
    /// Every distinct word of every field; guests refer to words by index, so each query word is scored once per
    /// distinct word rather than once per guest.
    private let words: [Bytes]
    private let inName: [Bool]
    private let inOther: [Bool]
    /// Per word: index into `equivalents` of the spellings of the same syllable in other systems (0 = none).
    private let wordEquivalents: [Int]
    private let equivalents: [[Bytes]]
    private let equivalentSets: [Set<Bytes>]
    /// Every distinct Han character found in a name, with all its spellings.
    private let hanSpellings: [[Bytes]]
    private let table: RomanTable

    public init(_ guests: [[String]]) {
        self.init(guests, table: .shared)
    }

    init(_ guests: [[String]], table: RomanTable) {
        self.table = table
        var wordID: [Bytes: Int32] = [:]
        var words: [Bytes] = []
        var inName: [Bool] = []
        var inOther: [Bool] = []
        var hanID: [UInt32: Int32] = [:]
        var hanSpellings: [[Bytes]] = []
        var indexed: [Indexed] = []
        indexed.reserveCapacity(guests.count)
        func intern(_ w: Bytes, name: Bool) -> Int32 {
            let id: Int32
            if let known = wordID[w] { id = known } else {
                id = Int32(words.count)
                wordID[w] = id
                words.append(w)
                inName.append(false)
                inOther.append(false)
            }
            if name { inName[Int(id)] = true } else { inOther[Int(id)] = true }
            return id
        }
        for fields in guests {
            var g = Indexed()
            for (f, field) in fields.enumerated() {
                let folded = Text.fold(field)
                g.literal.append(folded)
                var compact: Bytes = []
                for unit in Text.units(folded) {
                    switch unit {
                    case .word(let w):
                        let bytes = Bytes(w.utf8)
                        compact.append(contentsOf: bytes)
                        if f == 0 {
                            g.nameWords.append(intern(bytes, name: true))
                            if let first = bytes.first { g.initials.append(first) }
                        } else {
                            g.otherWords.append(intern(bytes, name: false))
                        }
                    case .han(let run):
                        for c in run {
                            if f == 0 {
                                g.nameHan.insert(table.fold(c))
                                if let id = hanID[c] { g.nameHanIDs.append(id) } else {
                                    let id = Int32(hanSpellings.count)
                                    hanID[c] = id
                                    hanSpellings.append(table.spellings(of: c).map { Bytes($0.utf8) })
                                    g.nameHanIDs.append(id)
                                }
                            } else {
                                g.otherHan.insert(table.fold(c))
                            }
                        }
                    case .symbol:
                        break
                    }
                }
                if f == 0 { g.compactName = compact } else if !compact.isEmpty { g.compactOther.append(compact) }
            }
            indexed.append(g)
        }
        var equivalents: [[Bytes]] = [[]]
        var sets: [Set<Bytes>] = [[]]
        var wordEquivalents = [Int](repeating: 0, count: words.count)
        for (id, w) in words.enumerated() where inName[id] {
            let set = table.equivalents(of: String(decoding: w, as: UTF8.self))
            if set.isEmpty { continue }
            let bytes = Set(set.map { Bytes($0.utf8) })
            wordEquivalents[id] = equivalents.count
            equivalents.append(bytes.sorted { $0.lexicographicallyPrecedes($1) })
            sets.append(bytes)
        }
        self.guests = indexed
        self.words = words
        self.inName = inName
        self.inOther = inOther
        self.wordEquivalents = wordEquivalents
        self.equivalents = equivalents
        self.equivalentSets = sets
        self.hanSpellings = hanSpellings
    }

    public func search(_ query: String) -> [Int] {
        let units = Text.units(Text.fold(String(query.unicodeScalars.prefix(Self.maxQueryScalars))))
            .prefix(Self.maxQueryUnits)
        if units.isEmpty { return Array(guests.indices) }
        let prepared: [Prepared] = units.map { unit in
            switch unit {
            case .word(let w): return prepareWord(Bytes(w.utf8))
            case .han(let run): return prepareHan(run)
            case .symbol(let s): return .symbol(s)
            }
        }
        var scored: [(Int, Int)] = []
        for (i, g) in guests.enumerated() {
            var total = 0
            var matched = true
            for unit in prepared {
                let s: Int
                switch unit {
                case .word(let w): s = score(w, g)
                case .han(let h): s = score(h, g)
                case .symbol(let sym): s = g.literal.contains { $0.contains(sym) } ? 50 : 0
                }
                if s == 0 { matched = false; break }
                total += s
            }
            if matched { scored.append((i, total)) }
        }
        scored.sort { $0.1 != $1.1 ? $0.1 > $1.1 : $0.0 < $1.0 }
        return scored.map(\.0)
    }

    static let maxQueryScalars = 200
    static let maxQueryUnits = 24
    /// Longest run of Han characters tried as one romanised word per group (子怡 -> Ziyi); longer runs still
    /// match character by character.
    static let maxGroupedRun = 16

    // MARK: Latin words

    private struct PreparedWord {
        let q: Bytes
        /// Per vocabulary word: its score as a name word and as another field's word (0 = no match).
        let name: [UInt8]
        let other: [UInt8]
        /// Per Han character in names: the score of q as one of its spellings.
        let han: [UInt8]
    }

    private func prepareWord(_ q: Bytes) -> Prepared {
        let n = q.count
        var eqHit = [UInt8](repeating: 0, count: equivalents.count)
        if n >= 2 {
            for e in 1 ..< equivalents.count {
                if equivalentSets[e].contains(q) { eqHit[e] = 75 }
                else if n >= 3 && Self.hasElement(withPrefix: q, in: equivalents[e]) { eqHit[e] = 60 }
            }
        }
        var name = [UInt8](repeating: 0, count: words.count)
        var other = [UInt8](repeating: 0, count: words.count)
        for (id, t) in words.enumerated() {
            let exact = t == q
            let prefix = !exact && Self.hasPrefix(t, q)
            let inside = !exact && !prefix && n >= 2 && Self.contains(t, q)
            if inName[id] {
                name[id] = exact ? 100 : prefix ? 85 : max(eqHit[wordEquivalents[id]], inside ? 50 : 0)
            }
            if inOther[id] {
                other[id] = exact ? 60 : prefix ? 50 : inside ? 30 : 0
            }
        }
        let han = hanSpellings.map { spellings -> UInt8 in
            if spellings.contains(q) { return 80 }
            return spellings.contains { Self.hasPrefix($0, q) } ? 60 : 0
        }
        return .word(PreparedWord(q: q, name: name, other: other, han: han))
    }

    private func score(_ w: PreparedWord, _ g: Indexed) -> Int {
        var best: UInt8 = 0
        for id in g.nameWords { best = max(best, w.name[Int(id)]) }
        for id in g.nameHanIDs { best = max(best, w.han[Int(id)]) }
        if best == 100 { return 100 }
        if w.q.count >= 2 && best < 55 {
            if Self.hasPrefix(g.initials, w.q) { best = 55 } else if Self.contains(g.initials, w.q) { best = max(best, 45) }
        }
        if best >= 60 { return Int(best) }
        for id in g.otherWords { best = max(best, w.other[Int(id)]) }
        if best == 0 && w.q.count >= 3 {
            if Self.contains(g.compactName, w.q) { best = 40 }
            else if g.compactOther.contains(where: { Self.contains($0, w.q) }) { best = 25 }
        }
        return Int(best)
    }

    // MARK: Han characters

    private struct PreparedHan {
        let chars: [UInt32]
        /// single[i][word]: name word `word` is one spelling of character i.
        let single: [[Bool]]
        /// group[i][k - 2][word]: characters i ..< i + k spell name word `word` together.
        let group: [[[Bool]]]
    }

    private func prepareHan(_ run: [UInt32]) -> Prepared {
        let spellings = run.map { c in Set(table.spellings(of: c).map { Bytes($0.utf8) }) }
        let single = spellings.map { set in words.indices.map { inName[$0] && set.contains(words[$0]) } }
        var group: [[[Bool]]] = []
        if run.count <= Self.maxGroupedRun {
            let lists = spellings.map { Array($0) }
            for i in run.indices {
                var byLength: [[Bool]] = []
                for k in 2 ... 4 where i + k <= run.count {
                    let members = Array(lists[i ..< i + k])
                    byLength.append(words.indices.map { inName[$0] && Self.spell(words[$0], with: members) })
                }
                group.append(byLength)
            }
        }
        return .han(PreparedHan(chars: run.map { table.fold($0) }, single: single, group: group))
    }

    /// Characters typed in a row: each matches the same character in a field, or (through its readings) a whole
    /// name word, or a group of up to four consecutive characters spells one name word together.
    private func score(_ h: PreparedHan, _ g: Indexed) -> Int {
        let n = h.chars.count
        var dp = [Int](repeating: -1, count: n + 1)
        dp[0] = 0
        for i in 1 ... n {
            let c = h.chars[i - 1]
            var best = -1
            if dp[i - 1] >= 0 {
                if g.nameHan.contains(c) { best = dp[i - 1] + 100 }
                else if g.nameWords.contains(where: { h.single[i - 1][Int($0)] }) { best = dp[i - 1] + 90 }
                else if g.otherHan.contains(c) { best = dp[i - 1] + 60 }
            }
            if !h.group.isEmpty && i >= 2 {
                for k in 2 ... min(4, i) where dp[i - k] >= 0 && dp[i - k] + 85 * k > best {
                    let hits = h.group[i - k][k - 2]
                    if g.nameWords.contains(where: { hits[Int($0)] }) { best = dp[i - k] + 85 * k }
                }
            }
            dp[i] = best
        }
        return max(dp[n], 0)
    }

    // MARK: Byte helpers

    /// Whether `word` is exactly one spelling of each group member in turn ("ziyi" = zi + yi).
    static func spell(_ word: Bytes, with group: [[Bytes]]) -> Bool {
        func go(_ pos: Int, _ k: Int) -> Bool {
            if k == group.count { return pos == word.count }
            for s in group[k] where pos + s.count <= word.count && word[pos ..< pos + s.count].elementsEqual(s) {
                if go(pos + s.count, k + 1) { return true }
            }
            return false
        }
        return go(0, 0)
    }

    static func hasPrefix(_ t: Bytes, _ q: Bytes) -> Bool {
        t.count >= q.count && t[0 ..< q.count].elementsEqual(q)
    }

    static func contains(_ t: Bytes, _ q: Bytes) -> Bool {
        guard !q.isEmpty, t.count >= q.count else { return q.isEmpty }
        let first = q[0]
        var i = 0
        while i <= t.count - q.count {
            if t[i] == first && t[i ..< i + q.count].elementsEqual(q) { return true }
            i += 1
        }
        return false
    }

    static func hasElement(withPrefix q: Bytes, in sorted: [Bytes]) -> Bool {
        var lo = 0, hi = sorted.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if sorted[mid].lexicographicallyPrecedes(q) { lo = mid + 1 } else { hi = mid }
        }
        return lo < sorted.count && hasPrefix(sorted[lo], q)
    }

    private enum Prepared {
        case word(PreparedWord)
        case han(PreparedHan)
        case symbol(String)
    }
}

private struct Indexed: Sendable {
    var nameWords: [Int32] = []
    var otherWords: [Int32] = []
    /// Han characters of the name (folded), and the same characters as `hanSpellings` indexes.
    var nameHan = Set<UInt32>()
    var nameHanIDs: [Int32] = []
    var otherHan = Set<UInt32>()
    var initials: [UInt8] = []
    /// The name's letters and digits without spaces, and the same for each other field.
    var compactName: [UInt8] = []
    var compactOther: [[UInt8]] = []
    /// Each field folded, for symbols (emoji…).
    var literal: [String] = []
}

enum Text {
    enum Unit: Equatable {
        case word(String)
        case han([UInt32])
        case symbol(String)
    }

    /// Lowercased, without diacritics, full-width forms made half-width.
    static func fold(_ s: String) -> String {
        s.folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: nil).lowercased()
    }

    static let apostrophes: Set<Unicode.Scalar> = ["'", "`", "\u{2018}", "\u{2019}", "\u{02BB}", "\u{02BC}", "\u{00B4}"]

    static func isWordScalar(_ u: Unicode.Scalar) -> Bool {
        u.properties.isAlphabetic || u.properties.numericType != nil
    }

    /// Splits folded text into words (letters and digits), runs of Han characters and other symbols (emoji…);
    /// spaces and punctuation separate.
    static func units(_ s: String) -> [Unit] {
        var out: [Unit] = []
        var word = String.UnicodeScalarView()
        var han: [UInt32] = []
        func flushWord() { if !word.isEmpty { out.append(.word(String(word))); word = String.UnicodeScalarView() } }
        func flushHan() { if !han.isEmpty { out.append(.han(han)); han = [] } }
        for u in s.unicodeScalars {
            let p = u.properties
            // Apostrophes inside a word are dropped, not separators: Wade-Giles ch'ang = chang, O'Brien = obrien.
            if !word.isEmpty && Self.apostrophes.contains(u) { continue }
            if p.isIdeographic || p.isUnifiedIdeograph {
                flushWord()
                han.append(u.value)
            } else if isWordScalar(u) {
                flushHan()
                word.append(u)
            } else if p.isWhitespace || p.isPatternSyntax || p.generalCategory.isPunctuationOrSeparator
                        || p.generalCategory == .nonspacingMark || p.generalCategory == .enclosingMark
                        || p.generalCategory == .format || p.generalCategory == .control {
                flushWord()
                flushHan()
            } else {
                flushWord()
                flushHan()
                out.append(.symbol(String(Character(u))))
            }
        }
        flushWord()
        flushHan()
        return out
    }
}

private extension Unicode.GeneralCategory {
    var isPunctuationOrSeparator: Bool {
        switch self {
        case .connectorPunctuation, .dashPunctuation, .openPunctuation, .closePunctuation, .initialPunctuation,
             .finalPunctuation, .otherPunctuation, .spaceSeparator, .lineSeparator, .paragraphSeparator:
            return true
        default:
            return false
        }
    }
}
