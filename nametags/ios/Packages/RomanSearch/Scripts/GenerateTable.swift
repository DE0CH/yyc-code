// Builds RomanSearch's romanisation table (run by Scripts/generate.sh, which downloads the pinned sources).
//
// Arguments: <Unihan dir> <chunlaw source.tsv> <CCC initials.csv> <CCC finals.csv> <LOC romcover.html>
//            <extracted Wade-Giles table out> <source label>
// Output (stdout), UTF-8, one record per line, tab-separated, trailing empty fields dropped:
//   S <jyutping syllable, toneless> <HK romanisations, space-separated>
//   W <pinyin syllable, toneless, ü written v> <Wade-Giles spellings, apostrophes and umlauts dropped>
//   <char> <jyutping readings> <pinyin readings (ü written v)> <extra HK spellings> <flags: c = common> <fold char>
// "fold" is the traditional form a simplified character is folded to when CJK text is compared with CJK text.
import Foundation

let args = CommandLine.arguments
guard args.count == 8 else {
    FileHandle.standardError.write("usage: generate <unihan dir> <chunlaw.tsv> <initials.csv> <finals.csv> <romcover.html> <wg out> <label>\n".data(using: .utf8)!)
    exit(2)
}
let unihanDir = args[1], chunlawPath = args[2], initialsPath = args[3], finalsPath = args[4]
let locPath = args[5], wadeGilesOut = args[6], label = args[7]

func readLines(_ path: String) -> [Substring] {
    guard let text = try? String(contentsOfFile: path, encoding: .utf8) else {
        FileHandle.standardError.write("cannot read \(path)\n".data(using: .utf8)!)
        exit(1)
    }
    return text.split(whereSeparator: { $0 == "\n" || $0 == "\r\n" })
}

// MARK: Unihan

struct Info {
    var jyutping: [String] = []
    var pinyin: [String] = []
    var hkExtra: [String] = []
    var common = false
    var traditional: [Unicode.Scalar] = []
}
var chars: [Unicode.Scalar: Info] = [:]

func scalar(_ codepoint: Substring) -> Unicode.Scalar? {
    guard codepoint.hasPrefix("U+"), let v = UInt32(codepoint.dropFirst(2), radix: 16) else { return nil }
    return Unicode.Scalar(v)
}

func appendUnique(_ list: inout [String], _ item: String) {
    if !item.isEmpty && !list.contains(item) { list.append(item) }
}

/// "zoeng1" -> "zoeng"
func tonelessJyutping<S: StringProtocol>(_ s: S) -> String {
    String(s.lowercased().filter { $0.isLetter })
}

/// "lǚ" -> "lv", "zhāng" -> "zhang"
func tonelessPinyin<S: StringProtocol>(_ s: S) -> String {
    var out = ""
    var lastWasU = false
    for u in String(s).lowercased().decomposedStringWithCanonicalMapping.unicodeScalars {
        if u.value == 0x308, lastWasU { out.removeLast(); out.append("v"); lastWasU = false; continue }
        if u.properties.generalCategory == .nonspacingMark { continue }
        if ("a"..."z").contains(Character(u)) { out.unicodeScalars.append(u); lastWasU = (u == "u") } else { lastWasU = false }
    }
    return out
}

let readingKeys: Set<Substring> = ["kCantonese", "kMandarin", "kTGHZ2013", "kXHC1983", "kGradeLevel", "kTraditionalVariant"]
for file in ["Unihan_Readings.txt", "Unihan_DictionaryLikeData.txt", "Unihan_Variants.txt"] {
    for line in readLines("\(unihanDir)/\(file)") where line.hasPrefix("U+") {
        let parts = line.split(separator: "\t", maxSplits: 2)
        guard parts.count == 3, readingKeys.contains(parts[1]), let c = scalar(parts[0]) else { continue }
        var info = chars[c] ?? Info()
        let value = parts[2]
        switch parts[1] {
        case "kCantonese":
            for r in value.split(separator: " ") { appendUnique(&info.jyutping, tonelessJyutping(r)) }
        case "kMandarin":
            for r in value.split(separator: " ") { appendUnique(&info.pinyin, tonelessPinyin(r)) }
        case "kTGHZ2013", "kXHC1983":
            // "404.190:xié 430.010:yè" (location:reading pairs)
            for item in value.split(separator: " ") {
                if let colon = item.lastIndex(of: ":") {
                    for r in item[item.index(after: colon)...].split(separator: ",") { appendUnique(&info.pinyin, tonelessPinyin(r)) }
                }
            }
            if parts[1] == "kTGHZ2013" { info.common = true }
        case "kGradeLevel":
            info.common = true
        case "kTraditionalVariant":
            info.traditional = value.split(separator: " ").compactMap { scalar($0.prefix { $0 != "<" }) }
        default: break
        }
        chars[c] = info
    }
}

// MARK: chunlaw/cantonese-romanisation (character -> HK romanisations, LSHK Jyutping)

var chunlawRoman: [Unicode.Scalar: [String]] = [:]
for line in readLines(chunlawPath).dropFirst() {
    let cols = line.split(separator: "\t", omittingEmptySubsequences: false)
    guard cols.count >= 3, cols[0].unicodeScalars.count == 1, let c = cols[0].unicodeScalars.first else { continue }
    var info = chars[c] ?? Info()
    for r in cols[2].split(separator: " ") { appendUnique(&info.jyutping, tonelessJyutping(r)) }
    var roman: [String] = []
    for r in cols[1].split(separator: " ") { appendUnique(&roman, r.lowercased().filter { $0.isLetter }) }
    chunlawRoman[c] = roman
    chars[c] = info
}

// Fill readings a simplified character lacks from its traditional form(s), and the reverse.
for (c, info) in chars {
    for t in info.traditional where t != c {
        guard var trad = chars[t] else { continue }
        var simp = chars[c]!
        if simp.jyutping.isEmpty { simp.jyutping = trad.jyutping }
        if trad.jyutping.isEmpty { trad.jyutping = simp.jyutping }
        if simp.pinyin.isEmpty { simp.pinyin = trad.pinyin }
        if trad.pinyin.isEmpty { trad.pinyin = simp.pinyin }
        if simp.common { trad.common = true }   // traditional forms of the common simplified characters
        chars[c] = simp
        chars[t] = trad
    }
}

// MARK: Jyutping -> HK government romanisation (Kataoka's table as encoded by CCCConverter)

func csvMap(_ path: String) -> [String: [String]] {
    var m: [String: [String]] = [:]
    for line in readLines(path).dropFirst() {
        let cols = line.split(separator: ",")
        guard cols.count == 2 else { continue }
        m[String(cols[0])] = cols[1].split(separator: "/").map(String.init)
    }
    return m
}
let initials = csvMap(initialsPath)
let finals = csvMap(finalsPath)
let initialOrder = initials.keys.sorted { $0.count > $1.count }
let sibilants: Set<String> = ["z", "c", "s"]

func hkSpellings(_ syllable: String) -> [String] {
    if let f = finals[syllable], syllable == "m" || syllable == "ng" { return f }   // syllabic nasals (吳 ng, 唔 m)
    var initial = ""
    for i in initialOrder where syllable.hasPrefix(i) && syllable.count > i.count { initial = i; break }
    let final = String(syllable.dropFirst(initial.count))
    guard var fs = finals[final] else { return [] }
    if final == "i" && !sibilants.contains(initial) { fs = fs.filter { !$0.hasPrefix("z") } }   // tsz/sze only after z c s
    guard !initial.isEmpty else { return fs }
    guard let ins = initials[initial] else { return [] }
    var out: [String] = []
    for i in ins { for f in fs { appendUnique(&out, i + f) } }
    return out
}

var syllables: Set<String> = []
for info in chars.values { syllables.formUnion(info.jyutping) }
var syllableMap: [String: [String]] = [:]
for s in syllables { syllableMap[s] = hkSpellings(s) }

// Spellings chunlaw gives for characters with a single toneless reading join that syllable's map (e.g. zau -> chow
// from 周 and 鄒; lei -> lee) when at least two such characters and a quarter of them agree, and the spelling starts
// with a consonant cluster Kataoka's table allows for that initial (or none, for initial j: 葉 Ip) and ends with the
// syllable's coda. chunlaw's lists also hold spellings of a character's other readings (zi -> jai, jip -> wa); those
// stay per character. Other chunlaw spellings stay per character too.
let vowels = Set("aeiouwy")
func cluster(_ s: String) -> String { String(s.prefix { !"aeiou".contains($0) }) }
func coda(_ s: String) -> String { String(String(s.reversed().prefix { !vowels.contains($0) }).reversed()) }
var votes: [String: [String: Int]] = [:]
var singles: [String: Int] = [:]
for (c, roman) in chunlawRoman {
    guard let info = chars[c], info.jyutping.count == 1 else { continue }
    singles[info.jyutping[0], default: 0] += 1
    for r in roman { votes[info.jyutping[0], default: [:]][r, default: 0] += 1 }
}
for (s, counts) in votes {
    let ccc = hkSpellings(s)
    guard !ccc.isEmpty else { continue }
    var clusters = Set(ccc.map(cluster))
    if s.hasPrefix("j") { clusters.insert("") }
    let codas = Set(ccc.map(coda))
    for (r, n) in counts.sorted(by: { $0.key < $1.key })
    where n >= 2 && 4 * n >= singles[s, default: 0] && r != s && clusters.contains(cluster(r)) && codas.contains(coda(r)) {
        appendUnique(&syllableMap[s, default: []], r)
    }
}
for (c, roman) in chunlawRoman {
    guard var info = chars[c] else { continue }
    var known = Set(info.jyutping)
    for s in info.jyutping { known.formUnion(syllableMap[s] ?? []) }
    for r in roman where !known.contains(r) { appendUnique(&info.hkExtra, r) }
    chars[c] = info
}

// MARK: Pinyin -> Wade-Giles (Library of Congress, "Correspondence of Wade-Giles to Pinyin")

// The page's table is two-column rows of <td> cells (Wade-Giles, Pinyin) after the "Wade-Giles" heading. Its ü
// survives only as U+FFFD (the page's encoding is broken), which is ü on both sides: v in Pinyin, u in Wade-Giles
// (umlauts are dropped for matching, as are the aspiration marks: ch`ang -> chang).
guard let locData = FileManager.default.contents(atPath: locPath) else {
    FileHandle.standardError.write("cannot read \(locPath)\n".data(using: .utf8)!)
    exit(1)
}
let loc = String(decoding: locData, as: UTF8.self)
guard let heading = loc.range(of: "<h4><em>Wade-Giles</em></h4>") else {
    FileHandle.standardError.write("LOC table not found\n".data(using: .utf8)!)
    exit(1)
}
var cells: [String] = []
var rest = loc[loc.range(of: "<td", options: .backwards, range: loc.startIndex ..< heading.lowerBound)!.lowerBound...]
while let open = rest.range(of: "<td"), let close = rest.range(of: "</td>", range: open.upperBound ..< rest.endIndex) {
    var cell = rest[open.upperBound ..< close.lowerBound].drop { $0 != ">" }.dropFirst()
    while let tag = cell.range(of: "<[^>]*>", options: .regularExpression) { cell.removeSubrange(tag) }
    cells.append(cell.replacingOccurrences(of: "&nbsp;", with: " ").trimmingCharacters(in: .whitespacesAndNewlines))
    rest = rest[close.upperBound...]
}
var wadeGiles: [String: [String]] = [:]
var extracted = ""
for k in stride(from: 2, to: cells.count - 1, by: 2) where cells[k] != "Wade-Giles" && !cells[k].isEmpty {
    let wg = cells[k], py = cells[k + 1]
    extracted += "\(wg)\t\(py)\n"
    let pinyin = String(py.lowercased().map { $0 == "\u{FFFD}" ? "v" : $0 }.filter { ("a"..."z").contains($0) })
    let spelling = String(wg.lowercased().map { $0 == "\u{FFFD}" ? "u" : $0 }.filter { ("a"..."z").contains($0) })
    if !pinyin.isEmpty && !spelling.isEmpty { appendUnique(&wadeGiles[pinyin, default: []], spelling) }
}
_ = FileManager.default.createFile(atPath: wadeGilesOut, contents: extracted.data(using: .utf8))
var pinyinSyllables: Set<String> = []
for info in chars.values { pinyinSyllables.formUnion(info.pinyin) }
let withoutWadeGiles = pinyinSyllables.filter { wadeGiles[$0] == nil }.sorted()
FileHandle.standardError.write("\(wadeGiles.count) pinyin syllables with Wade-Giles; in the data without: \(withoutWadeGiles.joined(separator: " "))\n".data(using: .utf8)!)

// MARK: Output

var out = "# RomanSearch romanisation table, generated by Scripts/generate.sh (do not edit). Sources: \(label).\n"
out += "# S<TAB>jyutping<TAB>HK romanisations | W<TAB>pinyin<TAB>Wade-Giles | char<TAB>jyutping<TAB>pinyin<TAB>extra HK<TAB>flags<TAB>fold\n"
for s in syllables.sorted() {
    let hk = (syllableMap[s] ?? []).filter { $0 != s }
    if !hk.isEmpty { out += "S\t\(s)\t\(hk.joined(separator: " "))\n" }
}
for p in wadeGiles.keys.sorted() {
    let wg = wadeGiles[p]!.filter { $0 != p }
    if !wg.isEmpty { out += "W\t\(p)\t\(wg.joined(separator: " "))\n" }
}
var written = 0
for c in chars.keys.sorted(by: { $0.value < $1.value }) {
    let info = chars[c]!
    guard !info.jyutping.isEmpty || !info.pinyin.isEmpty else { continue }
    let fold = info.traditional.first { $0 != c && !info.traditional.contains(c) }
        ?? info.traditional.first { $0 != c && chars[$0]?.traditional.isEmpty ?? true }
    var cols = [String(c), info.jyutping.joined(separator: " "), info.pinyin.joined(separator: " "),
                info.hkExtra.joined(separator: " "), info.common ? "c" : "", fold.map { String($0) } ?? ""]
    while let last = cols.last, last.isEmpty { cols.removeLast() }
    out += cols.joined(separator: "\t") + "\n"
    written += 1
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
FileHandle.standardError.write("\(syllables.count) jyutping syllables, \(written) characters\n".data(using: .utf8)!)
