import Foundation
import XCTest
@testable import RomanSearch

/// Synthetic guests only (no real guest data in git): name, institution, title, email, mobile.
let synthetic: [[String]] = [
    ["Mr Ki Cheung Oliver To", "Harbour Robotics Ltd", "CFO", "oliver.to@example.com", "9123 4567"],      // 0 杜琪章
    ["Ms Grace Fong Kwok Shan", "立法會秘書處", "Officer", "grace@example.org", ""],                       // 1 方國珊
    ["Prof Peter CHIU", "The Chinese University of Hong Kong", "Professor", "", ""],                     // 2 趙
    ["Jing HUANG", "Ministry of Service and Technology", "", "", ""],                                   // 3 黃靜
    ["Mr Wong Ching", "Kowloon Bank", "Manager", "", ""],                                               // 4 黃靜
    ["Dr Mary Chan Tai Man", "Hong Kong Medical Group", "Surgeon", "", ""],                             // 5 陳
    ["Mr Kwok Wing Hei", "Pearl Capital", "Analyst", "", ""],                                           // 6 郭
    ["Ms Cheung Mei Ling", "Lantau Foundation", "", "", ""],                                            // 7 張
    ["Mr Chow Siu Ming", "Tsing Yi Logistics", "", "", ""],                                             // 8 周
    ["Dr Lee Ka Yan", "Sai Kung Clinic", "", "", ""],                                                   // 9 李
    ["Ms Li Na", "Shenzhen Devices", "", "", ""],                                                       // 10 李
    ["Mr Ng Chi Keung", "Wan Chai Partners", "", "", ""],                                               // 11 吳
    ["Mr Ho Wai Kit", "Mong Kok Trading", "", "", ""],                                                  // 12 何
    ["Mr Zhao Lei", "Beijing Instruments", "", "", ""],                                                 // 13 趙 (pinyin)
    ["陈小明先生", "华永会计师事务所", "", "", ""],                                                      // 14 simplified
    ["Ms Ziyi Zhang", "Hangzhou Robotics", "", "", ""],                                                 // 15 子怡
    ["Dr José Müller", "Zürich Medical", "", "", ""],                                                   // 16 diacritics
    ["Ms Ip Siu Wai", "Kwun Tong Hospital", "", "", ""],                                                // 17 葉
    ["Mr Sze To Ka Ho", "Stanley Advisory", "", "", ""],                                                // 18 司徒
    ["Ms Lui Hoi Yan", "Tai Po Labs", "", "", ""],                                                      // 19 呂
    // Wade-Giles (Taiwan / older mainland spellings)
    ["Mr Jimmy Chang", "Taipei Ventures", "", "", ""],                                                  // 20 張
    ["Ms Mei-ling Chou", "Hsinchu Semiconductors", "", "", ""],                                         // 21 周
    ["Dr Chao Li-hua", "Tainan Clinic", "", "", ""],                                                    // 22 趙
    ["Mr Cheng Kuo-wei", "Kaohsiung Port", "", "", ""],                                                 // 23 鄭 國
    ["Ms Hsu Ya-ting", "Taichung Labs", "", "", ""],                                                    // 24 徐
    ["Prof Hsieh Chih-ming", "National Taiwan University", "", "", ""],                                 // 25 謝
    ["Mr Tsai Ming-hsien", "Keelung Shipping", "", "", ""],                                             // 26 蔡
    ["Ms Lin Shu-fen", "Taoyuan Airport", "", "", ""],                                                  // 27 林
    ["Mr Chiang Tzu-yang", "Hualien Tea", "", "", ""],                                                  // 28 江
    ["Dr Tseng Hsiao-wen", "Chiayi Hospital", "", "", ""],                                              // 29 曾
    ["Mr Ch'ang Ta-wei", "Pingtung Farms", "", "", ""],                                                 // 30 常
    ["Ms Tse Wing Sze", "Yuen Long Clinic", "", "", ""],                                                // 31 謝 (HK)
]

final class GuestSearchTests: XCTestCase {
    static let engine = GuestSearch(synthetic)
    var engine: GuestSearch { Self.engine }

    func top(_ q: String) -> Int? { engine.search(q).first }
    func found(_ q: String) -> Set<Int> { Set(engine.search(q)) }

    func testEmptyQueryReturnsAllInOrder() {
        XCTAssertEqual(engine.search(""), Array(synthetic.indices))
        XCTAssertEqual(engine.search("   "), Array(synthetic.indices))
        XCTAssertEqual(engine.search(" , . "), Array(synthetic.indices))
    }

    func testChineseNamesFindRomanisedGuests() {
        XCTAssertEqual(top("杜琪章"), 0)
        XCTAssertEqual(top("方國珊"), 1)
        XCTAssertEqual(top("方国珊"), 1)
        XCTAssertTrue(found("趙").isSuperset(of: [2, 13]))   // Chiu (HK), Zhao (pinyin)
        XCTAssertTrue(found("赵").isSuperset(of: [2, 13]))
        XCTAssertEqual(found("黃靜"), [3, 4])                 // Jing HUANG, Wong Ching
        XCTAssertTrue(found("黃").isSuperset(of: [3, 4]))
        XCTAssertTrue(found("黄").isSuperset(of: [3, 4]))
        XCTAssertTrue(found("陳").isSuperset(of: [5, 14]))   // Chan, 陈 (simplified in the data)
        XCTAssertTrue(found("郭").contains(6))
        XCTAssertTrue(found("張").isSuperset(of: [0, 7, 15])) // Cheung, Cheung, Zhang
        XCTAssertTrue(found("周").contains(8))
        XCTAssertTrue(found("李").isSuperset(of: [9, 10]))   // Lee, Li
        XCTAssertTrue(found("吳").contains(11))
        XCTAssertTrue(found("何").contains(12))
        XCTAssertTrue(found("杜").contains(0))
        XCTAssertTrue(found("琪").contains(0))              // Ki
        XCTAssertEqual(top("子怡"), 15)                     // one word spelled by two characters
        XCTAssertEqual(top("葉"), 17)                       // Ip
        XCTAssertEqual(top("司徒"), 18)                     // Sze To
        XCTAssertTrue(found("呂").contains(19))             // Lui
        XCTAssertEqual(top("陈小明"), 14)
        XCTAssertEqual(top("立法會"), 1)                    // CJK in another field
        XCTAssertEqual(found("黃 manager"), [4])           // mixed
    }

    func testLatinSyllablesInOtherSystems() {
        XCTAssertTrue(found("zoeng").isSuperset(of: [0, 7]))       // Jyutping -> Cheung
        XCTAssertTrue(found("zhang").isSuperset(of: [0, 7, 15]))   // pinyin -> Cheung, and Zhang
        XCTAssertEqual(top("zhang"), 15)                           // the exact spelling ranks first
        XCTAssertTrue(found("gwok").isSuperset(of: [1, 6]))        // Jyutping -> Kwok
        XCTAssertTrue(found("huang").isSuperset(of: [3, 4]))       // Huang, and Wong (黃)
        XCTAssertEqual(top("huang"), 3)
        XCTAssertTrue(found("zhao").isSuperset(of: [2, 13]))       // Chiu (趙)
        XCTAssertTrue(found("chen").contains(5))                   // Chan (陳)
        XCTAssertTrue(found("chan").contains(14))                  // 陈 in the data, typed in HK spelling
    }

    func testWadeGiles() {
        XCTAssertTrue(found("張").isSuperset(of: [0, 7, 15, 20]))   // Cheung, Cheung, Zhang, Chang
        XCTAssertTrue(found("周").isSuperset(of: [8, 21]))          // Chow, Chou
        XCTAssertTrue(found("趙").isSuperset(of: [2, 13, 22]))      // Chiu, Zhao, Chao
        XCTAssertTrue(found("鄭").contains(23))                     // Cheng
        XCTAssertEqual(top("鄭國"), 23)                             // Cheng Kuo
        XCTAssertTrue(found("徐").contains(24))                     // Hsu
        XCTAssertTrue(found("謝").isSuperset(of: [25, 31]))         // Hsieh, Tse
        XCTAssertTrue(found("蔡").contains(26))                     // Tsai
        XCTAssertTrue(found("林").contains(27))                     // Lin
        XCTAssertTrue(found("江").contains(28))                     // Chiang
        XCTAssertTrue(found("曾").contains(29))                     // Tseng
        XCTAssertTrue(found("常").isSuperset(of: [20, 30]))         // chang (Pinyin), Ch'ang in the data
        // Typed Latin Wade-Giles and the other systems' spellings of the same syllable
        XCTAssertTrue(found("zhou").isSuperset(of: [8, 21]))        // Pinyin -> Chow, Chou
        XCTAssertTrue(found("chou").contains(8))                    // Wade-Giles -> Chow
        XCTAssertTrue(found("xu").contains(24))                     // Pinyin -> Hsu
        XCTAssertEqual(top("hsu"), 24)
        XCTAssertTrue(found("xie").contains(25))                    // Pinyin -> Hsieh
        XCTAssertTrue(found("hsieh").isSuperset(of: [25, 31]))      // Wade-Giles -> Tse
        XCTAssertTrue(found("cai").contains(26))                    // Pinyin -> Tsai
        XCTAssertTrue(found("jiang").contains(28))                  // Pinyin -> Chiang
        XCTAssertTrue(found("zeng").contains(29))                   // Pinyin -> Tseng
        XCTAssertTrue(found("ch'ang").isSuperset(of: [20, 30]))     // apostrophe dropped
        XCTAssertTrue(found("ch’ang ta-wei").contains(30))
        XCTAssertTrue(found("tsai").contains(26))
        XCTAssertEqual(top("hsü"), 24)                              // umlaut dropped
    }

    func testWordsPrefixesSubstringsAndOrder() {
        XCTAssertEqual(top("oliver to"), 0)
        XCTAssertEqual(top("to oliver"), 0)
        XCTAssertEqual(top("oli"), 0)
        XCTAssertEqual(top("shan grace"), 1)
        XCTAssertEqual(top("CHIU peter"), 2)
        XCTAssertTrue(found("liver").contains(0))
        XCTAssertEqual(found("kco"), [0])                          // initials
        XCTAssertTrue(found("kcot").contains(0))
        XCTAssertEqual(found("harbour"), [0])
        XCTAssertEqual(found("cfo"), [0])
        XCTAssertEqual(found("oliver.to@example.com"), [0])
        XCTAssertEqual(found("9123"), [0])
        XCTAssertEqual(found("91234567"), [0])                     // spaces in the field ignored
        XCTAssertEqual(found("jose muller"), [16])
        XCTAssertEqual(found("MÜLLER"), [16])
        XCTAssertEqual(found("zurich"), [16])
        XCTAssertEqual(top("ＣＨＩＵ"), 2)                          // full-width
        XCTAssertEqual(found("nobody-like-this"), [])
    }

    func testOddInputDoesNotCrash() {
        XCTAssertEqual(engine.search("😀"), [])
        XCTAssertEqual(engine.search("👩‍👩‍👧‍👦 chan"), [])
        _ = engine.search(String(repeating: "a", count: 100_000))
        _ = engine.search(String(repeating: "黃", count: 10_000))
        _ = engine.search(String(repeating: "黃 chan ", count: 5_000))
        _ = engine.search("\u{0000}\u{FFFF}\u{200D}\u{0301}\u{E000}")
        _ = engine.search("𠀀𪚥龘")
        _ = engine.search("ﾁｬﾝ 한국어 русский عربى")
        let odd = GuestSearch([[], [""], ["😀"], [String(repeating: "x", count: 50_000)], ["\u{0301}"]])
        XCTAssertEqual(odd.search(""), [0, 1, 2, 3, 4])
        XCTAssertEqual(odd.search("😀"), [2])
        XCTAssertEqual(odd.search("xxx"), [3])
        XCTAssertEqual(GuestSearch([]).search("chan"), [])
    }

    func testFastEnoughForEveryKeystroke() {
        let surnames = ["Chan", "Wong", "Cheung", "Lee", "Lau", "Ng", "Ho", "Chow", "Kwok", "Huang", "Zhang", "Leung"]
        let given = ["Ka", "Wai", "Siu", "Ming", "Yan", "Chi", "Kit", "Ling", "Hoi", "Man", "Jing", "Wing"]
        var guests: [[String]] = []
        for i in 0 ..< 5000 {
            let name = "Mr \(given[i % 12]) \(given[(i / 12) % 12]) Person\(i) \(surnames[(i / 7) % 12])"
            guests.append([name, "Institution \(i % 97) Hong Kong", "Title \(i % 13)", "p\(i)@example.com", "\(90000000 + i)"])
        }
        let start = Date()
        let big = GuestSearch(guests)
        let built = Date().timeIntervalSince(start)
        var typed: [String] = []
        for q in ["chan tai man", "黃靜", "zhang wai", "kco", "hong kong", "person4999"] {
            for n in 1 ... q.count { typed.append(String(q.prefix(n))) }
        }
        let t0 = Date()
        for q in typed { _ = big.search(q) }
        let perQuery = Date().timeIntervalSince(t0) / Double(typed.count)
        print(String(format: "5000 guests: index %.0f ms, %.2f ms per keystroke over %d queries", built * 1000, perQuery * 1000, typed.count))
        XCTAssertLessThan(perQuery, 0.1)   // generous for debug builds on a shared CPU
    }

    func testTableLoaded() {
        XCTAssertGreaterThan(RomanTable.shared.characterCount, 40_000)
        XCTAssertTrue(RomanTable.shared.spellings(of: 0x9EC3).contains("wong"))   // 黃
        XCTAssertTrue(RomanTable.shared.spellings(of: 0x9EC3).contains("huang"))
    }
}
