import Foundation

/// The generated character table (Resources/romanisation.tsv): every Han character's Latin spellings
/// (Jyutping, Hong Kong government romanisation, Hanyu Pinyin, Wade-Giles), its fold target for CJK-to-CJK comparison,
/// and an index from spelling to the common characters that have it.
final class RomanTable: @unchecked Sendable {
    static let shared = RomanTable()

    /// Interned spellings.
    private(set) var spellings: [String] = []
    private var spellingID: [String: Int32] = [:]
    /// Per character entry: its spellings are `flat[offsets[e] ..< offsets[e + 1]]`.
    private var flat: [Int32] = []
    private var offsets: [Int32] = [0]
    private var entry: [UInt32: Int32] = [:]
    private var common: [Bool] = []
    private var foldTarget: [UInt32: UInt32] = [:]
    /// Per entry: end of its spellings without the extra per-character HK spellings.
    private var coreEnd: [Int32] = []
    /// Spelling id -> entries of the common characters with one reading in each language that have that spelling.
    private var commonBySpelling: [Int32: [Int32]] = [:]

    init(text: String) {
        load(text)
    }

    private convenience init() {
        let url = Bundle.module.url(forResource: "romanisation", withExtension: "tsv")
        let text = url.flatMap { try? String(contentsOf: $0, encoding: .utf8) } ?? ""
        self.init(text: text)
    }

    private func intern(_ s: String) -> Int32 {
        if let id = spellingID[s] { return id }
        let id = Int32(spellings.count)
        spellings.append(s)
        spellingID[s] = id
        return id
    }

    private func load(_ text: String) {
        var hk: [String: [String]] = [:]
        var wadeGiles: [String: [String]] = [:]
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            if line.hasPrefix("#") { continue }
            let cols = line.split(separator: "\t", omittingEmptySubsequences: false)
            if cols[0] == "S" {
                if cols.count >= 3 { hk[String(cols[1])] = cols[2].split(separator: " ").map(String.init) }
                continue
            }
            if cols[0] == "W" {
                if cols.count >= 3 { wadeGiles[String(cols[1])] = cols[2].split(separator: " ").map(String.init) }
                continue
            }
            guard cols[0].unicodeScalars.count == 1, let scalar = cols[0].unicodeScalars.first else { continue }
            var ids: [Int32] = []
            func add(_ s: String) {
                let id = intern(s)
                if !ids.contains(id) { ids.append(id) }
            }
            if cols.count > 1 {
                for j in cols[1].split(separator: " ") {
                    let s = String(j)
                    add(s)
                    for h in hk[s] ?? [] { add(h) }
                }
            }
            if cols.count > 2 {
                for p in cols[2].split(separator: " ") {
                    for w in wadeGiles[String(p)] ?? [] { add(w) }   // 張 zhang -> chang, 徐 xu -> hsu
                    if p.contains("v") {
                        // ü: lv -> lu, lv, lyu (吕 Lu / Lv / Lyu)
                        add(p.replacingOccurrences(of: "v", with: "u"))
                        add(String(p))
                        add(p.replacingOccurrences(of: "v", with: "yu"))
                    } else {
                        add(String(p))
                    }
                }
            }
            let core = ids.count
            if cols.count > 3 {
                for h in cols[3].split(separator: " ") { add(String(h)) }
            }
            let e = Int32(common.count)
            entry[scalar.value] = e
            coreEnd.append(Int32(flat.count + core))
            flat.append(contentsOf: ids)
            offsets.append(Int32(flat.count))
            let isCommon = cols.count > 4 && cols[4].contains("c")
            common.append(isCommon)
            // Only characters with a single reading in each language link a Cantonese spelling to a Mandarin one:
            // a polyphonic character's readings are not aligned in the data (方 fong and its rare readings).
            let monosyllabic = cols.count > 2 && !cols[1].contains(" ") && !cols[1].isEmpty
                && !cols[2].contains(" ") && !cols[2].isEmpty
            if isCommon && monosyllabic { for id in ids.prefix(core) { commonBySpelling[id, default: []].append(e) } }
            if cols.count > 5, let f = cols[5].unicodeScalars.first { foldTarget[scalar.value] = f.value }
        }
    }

    var characterCount: Int { common.count }

    /// The character a CJK character is compared as (simplified -> traditional).
    func fold(_ scalar: UInt32) -> UInt32 { foldTarget[scalar] ?? scalar }

    /// Every Latin spelling of a character (all readings, all systems), including those of its fold target.
    func spellings(of scalar: UInt32) -> [String] {
        var ids: [Int32] = []
        for s in Set([scalar, fold(scalar)]) {
            guard let e = entry[s] else { continue }
            for i in Int(offsets[Int(e)]) ..< Int(offsets[Int(e) + 1]) where !ids.contains(flat[i]) { ids.append(flat[i]) }
        }
        return ids.map { spellings[Int($0)] }
    }

    /// Spellings that denote the same sound as `token` in another system, through the common characters with one
    /// reading per language spelled `token` (cheung -> 張 章 … -> zoeng, zhang, …). Empty when there are none.
    func equivalents(of token: String) -> Set<String> {
        guard let id = spellingID[token], let entries = commonBySpelling[id] else { return [] }
        var out = Set<String>()
        for e in entries {
            for i in Int(offsets[Int(e)]) ..< Int(coreEnd[Int(e)]) { out.insert(spellings[Int(flat[i])]) }
        }
        out.remove(token)
        return out
    }
}
